import os
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_efectivo import (
    AHORA,
    TRATAMIENTOS,
    BaseEfectivo,
    item,
)
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento
from sistema.dentalink.models import Banco
from sistema.destinatarios.models import Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales import servicios as terminales
from sistema.terminales.models import Terminal
from sistema.terminales.servicios import abrir_sesion, crear_terminal

D = Decimal
AVISAR = "sistema.cobros.servicios.avisar_objetivo"
DUPLICADA = "Ya hay un cobro con ese número de operación en la terminal."


def admin():
    return get_user_model().objects.create_user(
        "adm", email="adm@example.com", role="administrator"
    )


class BasePos(BaseEfectivo):
    def setUp(self):
        super().setUp()
        p = mock.patch(TRATAMIENTOS, return_value=[tratamiento([prestacion(1, "900000", "0")])])
        p.start()
        self.addCleanup(p.stop)
        mp = Proveedor.objects.get(nombre="Mercadopago")
        rodrigo = Destinatario.objects.create(nombre="Rodrigo", tipo="clinica")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=rodrigo,
            titular="Rodrigo MP",
            proveedor=mp,
            objetivo=D("300000.00"),
        )
        self.smart = self.terminal("Point Smart #1", mp)
        self.mini = self.terminal("Point Mini #1", mp)
        self.banco = Banco.objects.create(id_dentalink=1, nombre="Banco Galicia")

    def terminal(self, nombre, proveedor, sesion=True, **metodos):
        metodos = metodos or {"acepta_debito": True, "acepta_credito": True, "acepta_qr": True}
        t = crear_terminal(usuario=None, nombre=nombre, proveedor=proveedor, **metodos)
        if sesion:
            abrir_sesion(t, self.cuenta, AHORA.replace(hour=9), usuario=None)
        return t

    def pos(self, metodo="debito", monto="30000.00", **kw):
        datos = {
            "terminal": self.smart,
            "monto_neto": D("29100.50"),
            "numero_operacion": "88213",
            "comprobante": imagen(),
        }
        if metodo in ("debito", "credito"):
            datos.update(banco_emisor=self.banco, ultimos_4="4321")
        datos.update(kw)
        return servicios.crear_cobro(
            usuario=self.ana,
            metodo=metodo,
            monto=D(monto),
            id_paciente=501,
            items=[item(1, monto)],
            **datos,
        )

    def rechaza(self, mensaje, **kw):
        with self.assertRaisesMessage(ValidationError, mensaje):
            self.pos(**kw)
        self.assertEqual(Cobro.objects.count(), 0)


class AltaPosTest(BasePos):
    def test_debito_queda_finalizado_en_la_cuenta_de_la_sesion(self):
        with mock.patch(AVISAR) as avisar:
            cobro = self.pos()
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")
        self.assertEqual(cobro.cuenta, self.cuenta)
        self.assertEqual(cobro.destinatario, self.cuenta.destinatario)
        self.assertEqual(cobro.terminal, self.smart)
        self.assertEqual((cobro.monto, cobro.monto_neto), (D("30000.00"), D("29100.50")))
        self.assertEqual(cobro.numero_operacion, "88213")
        self.assertEqual(cobro.banco_emisor.nombre, "Banco Galicia")
        self.assertEqual(cobro.ultimos_4, "4321")
        self.assertTrue(cobro.comprobante)
        self.assertEqual(cobro.items.count(), 1)
        avisar.assert_called_once_with(self.cuenta, D("0.00"))

    def test_credito_qr_y_link_tienen_un_alta(self):
        link = self.terminal("Link #1", self.smart.proveedor, acepta_link=True)
        self.assertEqual(self.pos("credito").metodo, "credito")
        qr = self.pos("qr", numero_operacion="2")
        self.assertEqual((qr.banco_emisor, qr.ultimos_4), (None, None))
        self.assertEqual(self.pos("link", terminal=link, numero_operacion="3").metodo, "link")

    def test_neto_igual_al_bruto_se_acepta_y_un_centavo_arriba_se_rechaza(self):
        self.assertEqual(self.pos(monto_neto=D("30000.00")).monto_neto, D("30000.00"))
        self.rechaza_otro(monto_neto=D("30000.01"), numero_operacion="2")

    def rechaza_otro(self, **kw):
        n = Cobro.objects.count()
        with self.assertRaisesMessage(ValidationError, "El neto debe ser mayor que $0,00"):
            self.pos(**kw)
        self.assertEqual(Cobro.objects.count(), n)

    def test_neto_de_un_centavo_se_acepta_y_cero_se_rechaza(self):
        self.assertEqual(self.pos(monto_neto=D("0.01")).monto_neto, D("0.01"))
        self.rechaza_otro(monto_neto=D("0.00"), numero_operacion="2")

    def test_credito_con_operacion_de_3_digitos_en_ultimos_4_se_rechaza(self):
        self.rechaza("Cargá el banco emisor", metodo="credito", ultimos_4="432")

    def test_qr_sin_banco_ni_ultimos_4_se_acepta_y_con_banco_se_rechaza(self):
        self.pos("qr")
        n = Cobro.objects.count()
        with self.assertRaisesMessage(ValidationError, "Cargá el banco emisor"):
            self.pos("qr", numero_operacion="2", banco_emisor=self.banco)
        self.assertEqual(Cobro.objects.count(), n)

    def test_tarjeta_sin_comprobante_se_rechaza(self):
        self.rechaza("Falta el comprobante.", comprobante=None)

    def test_banco_no_vigente_se_rechaza(self):
        self.banco.vigente = False
        self.banco.save()
        self.rechaza("Elegí un banco vigente de la copia de Dentalink.")

    def test_sin_terminal_se_rechaza(self):
        self.rechaza("Elegí la terminal POS.", terminal=None)


class TerminalTest(BasePos):
    def test_operacion_repetida_en_la_misma_terminal_se_rechaza(self):
        self.pos()
        n = Cobro.objects.count()
        with self.assertRaisesMessage(ValidationError, DUPLICADA):
            self.pos()
        self.assertEqual(Cobro.objects.count(), n)

    def test_operacion_con_espacios_se_compara_igual(self):
        self.pos()
        with self.assertRaisesMessage(ValidationError, DUPLICADA):
            self.pos(numero_operacion=" 88 213")

    def test_misma_operacion_en_otra_terminal_se_acepta(self):
        self.pos()
        self.assertEqual(self.pos(terminal=self.mini).terminal, self.mini)

    def test_operacion_de_un_cobro_cancelado_se_reutiliza(self):
        primero = self.pos()
        servicios.cancelar_cobro(
            primero, usuario=admin(), motivo="otro_medio", estado_visto="finalizado"
        )
        self.assertEqual(self.pos().numero_operacion, "88213")

    def test_terminal_que_no_acepta_link_se_rechaza(self):
        payway = Proveedor.objects.get(nombre="Payway")
        cuenta = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=self.cuenta.destinatario,
            titular="Rodrigo Payway",
            proveedor=payway,
            objetivo=D("300000.00"),
        )
        t = self.terminal("Payway #1", payway, sesion=False, acepta_debito=True)
        abrir_sesion(t, cuenta, AHORA.replace(hour=9), usuario=None)
        self.rechaza("La terminal no acepta Link de pago.", metodo="link", terminal=t)

    def test_terminal_inactiva_se_rechaza(self):
        t = self.terminal("Point Plus #1", self.smart.proveedor, acepta_debito=True)
        Terminal.objects.filter(pk=t.pk).update(estado="inactiva")
        t.refresh_from_db()
        self.rechaza("La terminal no está Activa.", terminal=t)

    def test_terminal_sin_sesion_se_rechaza(self):
        t = self.terminal("Point Plus #1", self.smart.proveedor, sesion=False, acepta_debito=True)
        self.rechaza("La terminal no tiene una sesión en esa fecha y hora.", terminal=t)


class ObjetivoTest(BasePos):
    def cobrar_hasta(self, monto):
        cobro_prueba(
            cuenta=self.cuenta,
            metodo="qr",
            estado="finalizado",
            monto=D(monto),
            terminal=self.smart,
            fecha=AHORA,
        )

    def test_un_centavo_que_cruza_el_objetivo_avisa(self):
        self.cobrar_hasta("299999.99")
        with mock.patch(AVISAR, wraps=servicios.avisar_objetivo) as avisar:
            self.pos(monto="0.01", monto_neto=D("0.01"))
        avisar.assert_called_once_with(self.cuenta, D("299999.99"))

    def test_avisar_objetivo_devuelve_true_al_cruzar_y_false_si_no(self):
        self.cobrar_hasta("299999.99")
        antes = terminales.acumulado(self.cuenta)
        self.pos(monto="0.01", monto_neto=D("0.01"))
        self.assertTrue(terminales.avisar_objetivo(self.cuenta, antes))
        self.assertFalse(terminales.avisar_objetivo(self.cuenta, antes + D("0.01")))

    def test_cancelar_el_cobro_pos_llama_avisar_objetivo(self):
        cobro = self.pos()
        with mock.patch(AVISAR, wraps=servicios.avisar_objetivo) as avisar:
            servicios.cancelar_cobro(
                cobro, usuario=admin(), motivo="otro_medio", estado_visto="finalizado"
            )
        avisar.assert_called_once_with(self.cuenta, D("30000.00"))


class ArchivoHuerfanoTest(BasePos):
    def archivos(self):
        carpeta = os.path.join(settings.MEDIA_ROOT, "comprobantes")
        return [f for _, _, fs in os.walk(carpeta) for f in fs]

    def test_falla_despues_de_escribir_el_comprobante_no_deja_archivo(self):
        antes = len(self.archivos())
        with (
            mock.patch("sistema.cobros.servicios.dentalink.encolar_envio", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            self.pos()
        self.assertEqual(Cobro.objects.count(), 0)
        self.assertEqual(len(self.archivos()), antes)

    def test_alta_correcta_deja_un_archivo(self):
        antes = len(self.archivos())
        self.pos()
        self.assertEqual(len(self.archivos()), antes + 1)
