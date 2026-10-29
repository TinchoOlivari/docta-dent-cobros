from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail

from sistema.avisos.models import ConfiguracionAvisos, DireccionAviso
from sistema.avisos.tests import AvisosTestCase
from sistema.cobros import servicios
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_efectivo import AHORA
from sistema.cobros.tests.test_pos import BasePos, admin
from sistema.destinatarios.models import Destinatario
from sistema.destinatarios.servicios import crear_cuenta, editar_cuenta
from sistema.terminales.models import Sesion
from sistema.terminales.servicios import cambiar_acumula_desde, cambiar_cuenta, corregir_cambio

D = Decimal
DELAY = "sistema.dentalink.tasks.enviar_cobro.delay"
CERCA = "Cuenta Rodrigo — Rodrigo MP cerca del objetivo"
ALCANZO = "Cuenta Rodrigo — Rodrigo MP alcanzó su objetivo"


class BaseObjetivo(AvisosTestCase, BasePos):
    def setUp(self):
        super().setUp()
        for email in ("duenio@clinica.test", "admin@clinica.test"):
            DireccionAviso.objects.create(email=email)

    def hasta(self, monto):
        cobro_prueba(
            cuenta=self.cuenta,
            metodo="qr",
            estado="finalizado",
            monto=D(monto),
            terminal=self.smart,
            fecha=AHORA,
        )

    def cobrar(self, monto):
        with mock.patch(DELAY), self.captureOnCommitCallbacks(execute=True):
            return self.pos(monto=monto, monto_neto=D(monto))

    def asuntos(self):
        return [m.subject for m in mail.outbox]


class ObjetivoTests(BaseObjetivo):
    def test_cruza_el_umbral_avisa_cerca_con_los_datos(self):
        self.hasta("260000.00")
        self.cobrar("10000.00")  # 270000: justo el umbral
        self.assertEqual(self.asuntos(), [CERCA] * 2)
        cuerpo = mail.outbox[0].body
        for texto in (
            "Point Mini #1, Point Smart #1",
            "$300.000,00",
            "$270.000,00",
            "Acumula desde:",
            "Prepará el cambio de cuenta en la terminal.",
        ):
            self.assertIn(texto, cuerpo)
        self.assertNotIn("alcanzó", " ".join(self.asuntos()))

    def test_un_centavo_antes_del_umbral_no_avisa(self):
        self.hasta("260000.00")
        self.cobrar("9999.99")
        self.assertEqual(mail.outbox, [])

    def test_alcanza_el_objetivo_justo_avisa(self):
        self.hasta("290000.00")
        self.cobrar("10000.00")
        self.assertEqual(self.asuntos(), [ALCANZO] * 2)
        self.assertIn(
            "Cambiá la cuenta en la terminal y creá la sesión nueva.", mail.outbox[0].body
        )

    def test_un_centavo_antes_del_objetivo_no_avisa_alcanzado(self):
        self.hasta("290000.00")
        self.cobrar("9999.99")
        self.assertEqual(mail.outbox, [])

    def test_un_cobro_que_cruza_umbral_y_objetivo_manda_dos_avisos_por_direccion(self):
        self.hasta("260000.00")
        self.cobrar("40000.00")
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(sorted(set(self.asuntos())), sorted([CERCA, ALCANZO]))

    def test_sin_sesion_abierta_dice_ninguna(self):
        Sesion.objects.update(fin=AHORA - timedelta(minutes=1))
        self.hasta("260000.00")
        from sistema.avisos import servicios as avisos

        with self.captureOnCommitCallbacks(execute=True):
            avisos.cerca_del_objetivo(
                cuenta=self.cuenta, antes=D("260000.00"), despues=D("270000.00")
            )
        self.assertIn("Terminales con sesión abierta: ninguna", mail.outbox[0].body)


class MargenCeroTests(BaseObjetivo):
    def test_margen_cero_y_objetivo_justo_manda_cerca_y_alcanzado(self):
        ConfiguracionAvisos.objects.update_or_create(pk=1, defaults={"margen": 0})
        self.hasta("299999.99")
        self.cobrar("0.01")
        self.assertEqual(sorted(set(self.asuntos())), sorted([CERCA, ALCANZO]))
        self.assertEqual(len(mail.outbox), 4)


class SinAvisoTests(BaseObjetivo):
    def test_cambiar_objetivo_no_avisa(self):
        self.hasta("260000.00")
        with self.captureOnCommitCallbacks(execute=True):
            editar_cuenta(self.cuenta, usuario=None, objetivo=D("250000.00"))
        self.assertEqual(mail.outbox, [])

    def test_cambiar_acumula_desde_no_avisa(self):
        self.hasta("260000.00")
        with self.captureOnCommitCallbacks(execute=True):
            cambiar_acumula_desde(self.cuenta, AHORA - timedelta(hours=1), usuario=None)
        self.assertEqual(mail.outbox, [])

    def test_cancelar_un_cobro_pos_no_avisa(self):
        cobro = self.cobrar("30000.00")
        with self.captureOnCommitCallbacks(execute=True):
            servicios.cancelar_cobro(
                cobro, usuario=admin(), motivo="otro_medio", estado_visto="finalizado"
            )
        self.assertEqual(mail.outbox, [])


class CorreccionTests(BaseObjetivo):
    def test_corregir_el_cambio_que_lleva_la_cuenta_nueva_sobre_el_objetivo_avisa(self):
        jimena = Destinatario.objects.create(nombre="Jimena", tipo="clinica")
        b = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=jimena,
            titular="Jimena MP",
            proveedor=self.cuenta.proveedor,
            objetivo=D("300000.00"),
        )
        desde = AHORA - timedelta(days=1)
        type(b).objects.filter(pk=b.pk).update(acumula_desde=desde)
        sesion_a = Sesion.objects.get(terminal=self.smart)
        cambiar_cuenta(self.smart, b, AHORA.replace(hour=9, minute=30), usuario=None)
        sesion_a.refresh_from_db()
        for cuenta, monto, hora in ((b, "290000.00", 45), (self.cuenta, "20000.00", 15)):
            cobro_prueba(
                cuenta=cuenta,
                destinatario=cuenta.destinatario,
                metodo="qr",
                estado="finalizado",
                monto=D(monto),
                terminal=self.smart,
                fecha=AHORA.replace(hour=9, minute=hora),
            )
        with self.captureOnCommitCallbacks(execute=True):
            corregir_cambio(sesion_a, AHORA.replace(hour=9, minute=10), usuario=None)
        self.assertEqual(self.asuntos(), ["Cuenta Jimena — Jimena MP alcanzó su objetivo"] * 2)
