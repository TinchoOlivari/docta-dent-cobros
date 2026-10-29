from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from sistema.auditoria.models import Evento
from sistema.cobros import servicios
from sistema.cobros.models import Cobro, ItemCobro
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG, rango_mes_arg
from sistema.dentalink.cliente import DentalinkNoDisponible
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento
from sistema.destinatarios.models import Cuenta, Destinatario

D = Decimal
AHORA = datetime(2026, 10, 14, 10, 0, tzinfo=ARG)
TRATAMIENTOS = "sistema.dentalink.cliente.tratamientos"
PACIENTE = "sistema.dentalink.cliente.paciente"
DELAY = "sistema.dentalink.tasks.enviar_cobro.delay"
DATOS_PACIENTE = {"id": 501, "nombre": "Juan Paciente", "dni": "30111222"}


def item(id_prestacion, monto):
    return {
        "id_prestacion": id_prestacion,
        "monto": D(monto),
        "id_profesional": None,
        "saldo_a_favor": False,
    }


class BaseEfectivo(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        self.reloj.start()
        self.addCleanup(self.reloj.stop)
        for nombre, valor in (
            (
                TRATAMIENTOS,
                [tratamiento([prestacion(1, "40000", "0"), prestacion(2, "25000", "0")])],
            ),
        ):
            p = mock.patch(nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch(PACIENTE, return_value=DATOS_PACIENTE)
        self.paciente = p.start()
        self.addCleanup(p.stop)
        self.ana = get_user_model().objects.create_user(
            "ana", email="ana@example.com", role="operator"
        )
        dest = Destinatario.objects.create(nombre="Clínica", tipo="clinica", cuit="20000000001")
        self.efectivo = Cuenta.objects.create(destinatario=dest, tipo="efectivo")

    def crear(self, items=None, monto="65000.00", **kw):
        items = items if items is not None else [item(1, "40000.00"), item(2, "25000.00")]
        return servicios.crear_cobro(
            usuario=self.ana,
            metodo="efectivo",
            monto=D(monto),
            id_paciente=501,
            items=items,
            **kw,
        )


class EfectivoTest(BaseEfectivo):
    def test_dos_items_dejan_el_cobro_finalizado_en_efectivo(self):
        with mock.patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            cobro = self.crear()
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")
        self.assertEqual(cobro.monto, D("65000.00"))
        self.assertEqual(cobro.cuenta, self.efectivo)
        self.assertEqual(cobro.destinatario, self.efectivo.destinatario)
        self.assertEqual(cobro.creado_por, self.ana)
        self.assertEqual(cobro.fecha, AHORA)
        self.assertEqual(
            (cobro.id_paciente_dentalink, cobro.nombre_paciente, cobro.dni_paciente),
            (501, "Juan Paciente", "30111222"),
        )
        self.assertEqual([i.numero for i in cobro.items.order_by("numero")], [1, 2])
        self.assertEqual(
            Evento.objects.filter(tipo="alta", entidad__startswith="cobros.").count(), 3
        )
        delay.assert_called_once_with(cobro.pk)

    def test_sin_cuenta_de_efectivo_activa_rechaza(self):
        self.efectivo.estado = "inactiva"
        self.efectivo.save()
        with self.assertRaisesMessage(ValidationError, "No hay una cuenta de efectivo Activa."):
            self.crear()
        self.assertEqual(Cobro.objects.count(), 0)

    def test_comprobante_de_efectivo_se_ignora(self):
        archivo = SimpleUploadedFile("c.pdf", b"%PDF-1.4", content_type="application/pdf")
        cobro = self.crear(comprobante=archivo)
        self.assertFalse(cobro.comprobante)

    def test_monto_un_centavo_arriba_de_los_items_rechaza(self):
        with self.assertRaisesMessage(ValidationError, "no es igual a la suma"):
            self.crear(monto="65000.01")
        self.assertEqual(Cobro.objects.count(), 0)

    def test_monto_un_centavo_abajo_de_los_items_rechaza(self):
        with self.assertRaisesMessage(ValidationError, "no es igual a la suma"):
            self.crear(monto="64999.99")

    def test_monto_cero_rechaza(self):
        with self.assertRaisesMessage(ValidationError, "El monto debe ser mayor que $0,00."):
            self.crear(items=[item(1, "40000.00")], monto="0.00")

    def test_item_en_el_saldo_exacto_se_acepta_y_un_centavo_mas_no(self):
        self.assertEqual(self.crear(items=[item(1, "40000.00")], monto="40000.00").items.count(), 1)
        with self.assertRaisesMessage(ValidationError, "supera el saldo disponible"):
            self.crear(items=[item(2, "25000.01")], monto="25000.01")

    def test_numero_crece_con_cada_alta(self):
        a = self.crear(items=[item(1, "100.00")], monto="100.00")
        b = self.crear(items=[item(2, "100.00")], monto="100.00")
        self.assertGreater(b.pk, a.pk)

    def test_observaciones_se_guardan(self):
        self.assertEqual(
            self.crear(observaciones="Pagó en mostrador").observaciones, "Pagó en mostrador"
        )

    def test_un_alta_encola_un_solo_envio(self):
        with mock.patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            self.crear()
        self.assertEqual(delay.call_count, 1)


class RollbackTest(BaseEfectivo):
    def test_segundo_item_que_falla_deshace_todo(self):
        original = ItemCobro.full_clean
        llamadas = []

        def falla_el_segundo(self_, *a, **kw):
            llamadas.append(1)
            if len(llamadas) == 2:
                raise ValidationError("falla")
            return original(self_, *a, **kw)

        with mock.patch.object(ItemCobro, "full_clean", falla_el_segundo):
            with self.assertRaises(ValidationError):
                self.crear()
        self.assertEqual(Cobro.objects.count(), 0)
        self.assertEqual(ItemCobro.objects.count(), 0)
        self.assertEqual(Evento.objects.filter(tipo="alta").count(), 0)

    def test_dentalink_caido_no_crea_cobro(self):
        with mock.patch(TRATAMIENTOS, side_effect=DentalinkNoDisponible()):
            with self.assertRaises(DentalinkNoDisponible):
                self.crear()
        self.assertEqual(Cobro.objects.count(), 0)

    def test_el_cobro_conserva_su_cuenta_si_cambia_la_activa(self):
        cobro = self.crear()
        self.efectivo.estado = "inactiva"
        self.efectivo.save()
        otra = Cuenta.objects.create(destinatario=self.efectivo.destinatario, tipo="efectivo")
        cobro.refresh_from_db()
        self.assertEqual(cobro.cuenta, self.efectivo)
        self.assertNotEqual(cobro.cuenta, otra)


class FechaTest(BaseEfectivo):
    def alta_en(self, momento):
        with mock.patch("django.utils.timezone.now", return_value=momento):
            return self.crear(items=[item(1, "100.00")], monto="100.00")

    def test_ultimo_segundo_de_octubre_es_octubre(self):
        cobro = self.alta_en(datetime(2026, 10, 31, 23, 59, 59, tzinfo=ARG))
        self.assertEqual(rango_mes_arg(cobro.fecha)[0].month, 10)

    def test_primer_segundo_de_noviembre_es_noviembre(self):
        cobro = self.alta_en(datetime(2026, 11, 1, 0, 0, 0, tzinfo=ARG))
        self.assertEqual(rango_mes_arg(cobro.fecha)[0].month, 11)
