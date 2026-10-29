from datetime import date
from decimal import Decimal
from unittest import mock

from django.db import connection

from sistema.arca import servicios as arca
from sistema.arca.models import ComprobanteFiscal
from sistema.arca.tests.fabricas import cobro_facturable, emisor_prueba
from sistema.auditoria.models import Evento
from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_efectivo import DELAY, TRATAMIENTOS, BaseEfectivo, item
from sistema.cobros.tests.test_faltan_items import FaltanItemsBase
from sistema.cobros.tests.test_pos import BasePos
from sistema.cobros.tests.test_transferencia import BaseTransferencia
from sistema.dentalink.models import ConfiguracionDentalink
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento
from sistema.destinatarios.models import Destinatario

D = Decimal
EMITIR = "sistema.arca.tasks.emitir.delay"


def activar_facturacion(destinatario):
    """Facturación activa con un emisor Activo, sin pasar por el servicio de 001."""
    emisor = emisor_prueba()
    Destinatario.objects.filter(pk=destinatario.pk).update(facturacion_activa=True, emisor=emisor)
    return emisor


class Fixture:
    """Un doble de `emitir.delay` y del envío a Dentalink por test."""

    def setUp(self):
        super().setUp()
        for nombre in (EMITIR, DELAY):
            parche = mock.patch(nombre)
            mock_ = parche.start()
            self.addCleanup(parche.stop)
            if nombre == EMITIR:
                self.emitir = mock_

    def facturas(self):
        return ComprobanteFiscal.objects.all()


class EfectivoTest(Fixture, BaseEfectivo):
    def test_efectivo_de_65000_con_facturacion_crea_una_factura_pendiente(self):
        emisor = activar_facturacion(self.efectivo.destinatario)
        with self.captureOnCommitCallbacks(execute=True):
            cobro = self.crear()
        (factura,) = self.facturas()
        self.assertEqual(cobro.monto, D("65000.00"))
        self.assertEqual(factura.cobro, cobro)
        self.assertEqual(factura.emisor, emisor)
        self.assertEqual(factura.tipo, "factura")
        self.assertEqual(factura.estado, "pendiente")
        self.assertTrue(factura.vigente)
        self.assertEqual(factura.concepto, "servicios")
        self.assertEqual(factura.descripcion, "Servicios odontológicos")
        self.emitir.assert_called_once_with(factura.pk)

    def test_la_factura_deja_un_evento_de_alta_del_sistema(self):
        activar_facturacion(self.efectivo.destinatario)
        self.crear()
        evento = Evento.objects.get(tipo="alta", entidad__startswith="arca.comprobantefiscal")
        self.assertIsNone(evento.usuario)
        self.assertEqual(evento.origen, "facturación automática")

    def test_sin_facturacion_no_crea_factura(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.crear()
        self.assertEqual(self.facturas().count(), 0)
        self.emitir.assert_not_called()

    def test_la_factura_toma_concepto_y_descripcion_del_emisor(self):
        emisor = activar_facturacion(self.efectivo.destinatario)
        emisor.concepto, emisor.descripcion = "productos", "Insumos"
        emisor.save()
        self.crear()
        factura = self.facturas().get()
        self.assertEqual((factura.concepto, factura.descripcion), ("productos", "Insumos"))

    def test_emisor_inactivo_la_factura_nace_pendiente(self):
        emisor = activar_facturacion(self.efectivo.destinatario)
        type(emisor).objects.filter(pk=emisor.pk).update(estado="inactivo")
        self.crear()
        self.assertEqual(self.facturas().get().estado, "pendiente")

    def test_desactivar_la_facturacion_despues_no_cambia_la_factura_creada(self):
        activar_facturacion(self.efectivo.destinatario)
        cobro = self.crear()
        Destinatario.objects.filter(pk=self.efectivo.destinatario.pk).update(
            facturacion_activa=False
        )
        cobro.refresh_from_db()
        self.assertIsNone(arca.al_finalizar(cobro))
        (factura,) = self.facturas()
        self.assertEqual((factura.estado, factura.vigente), ("pendiente", True))

    def test_falla_al_crear_factura_no_deshace_el_cobro(self):
        activar_facturacion(self.efectivo.destinatario)
        with (
            mock.patch.object(ComprobanteFiscal, "full_clean", side_effect=RuntimeError),
            self.assertLogs("sistema.arca.servicios", level="ERROR") as logs,
        ):
            cobro = self.crear()
        self.assertIn("Factura no creada", logs.output[0])
        self.assertTrue(Cobro.objects.filter(pk=cobro.pk, estado="finalizado").exists())
        self.assertEqual(self.facturas().count(), 0)
        self.emitir.assert_not_called()

    def test_falla_de_la_base_al_crear_la_factura_no_rompe_la_transaccion_del_cobro(self):
        activar_facturacion(self.efectivo.destinatario)

        def division_por_cero(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1/0")

        with (
            mock.patch.object(ComprobanteFiscal, "save", division_por_cero),
            self.assertLogs("sistema.arca.servicios", level="ERROR"),
        ):
            cobro = self.crear()
        # Si el savepoint no deshiciera el error, esta lectura fallaría.
        self.assertTrue(Cobro.objects.filter(pk=cobro.pk, estado="finalizado").exists())
        self.assertEqual(self.facturas().count(), 0)

    def test_llamar_dos_veces_deja_una_sola_factura(self):
        activar_facturacion(self.efectivo.destinatario)
        cobro = self.crear()
        self.assertIsNone(arca.al_finalizar(cobro))
        self.assertEqual(self.facturas().count(), 1)

    def test_cobro_de_40000_con_item_de_35000_y_saldo_a_favor_de_5000_crea_una_factura(self):
        ConfiguracionDentalink.objects.create(
            pk=1, id_prestacion_saldo_a_favor=9100, nombre_prestacion_saldo_a_favor="Saldo a favor"
        )
        activar_facturacion(self.efectivo.destinatario)
        saldo = {"id_prestacion": None, "monto": D("5000.00"), "saldo_a_favor": True}
        tratamientos = [
            tratamiento(
                [
                    prestacion(1, "40000", "0"),
                    prestacion(9050, "0.00", "0.00", id_prestacion=9100),
                ]
            )
        ]
        with mock.patch(TRATAMIENTOS, return_value=tratamientos):
            cobro = self.crear(items=[item(1, "35000.00"), saldo], monto="40000.00")
        self.assertEqual(cobro.items.count(), 2)
        self.assertEqual(self.facturas().count(), 1)


class FacturableTest(Fixture, BaseEfectivo):
    def setUp(self):
        super().setUp()
        self.dest = self.efectivo.destinatario
        activar_facturacion(self.dest)
        self.dest.refresh_from_db()

    def cobro(self, **campos):
        return cobro_facturable(**({"destinatario": self.dest} | campos))

    def test_finalizado_de_un_destinatario_con_facturacion_es_facturable(self):
        self.assertTrue(arca.facturable(self.cobro()))

    def test_migrado_no_es_facturable_y_no_crea_factura(self):
        migrado = self.cobro(migrado=True)
        self.assertFalse(arca.facturable(migrado))
        self.assertIsNone(arca.al_finalizar(migrado))
        self.assertEqual(self.facturas().count(), 0)
        self.assertIsNotNone(
            arca.al_finalizar(self.cobro())
        )  # control: el mismo cobro sin la marca

    def test_faltan_items_no_es_facturable(self):
        self.assertFalse(arca.facturable(self.cobro(faltan_items=True)))

    def test_cancelado_no_es_facturable(self):
        cobro = cobro_prueba(
            cuenta=self.efectivo,
            destinatario=self.dest,
            metodo="efectivo",
            monto=D("65000.00"),
            estado="cancelado",
        )
        self.assertFalse(arca.facturable(cobro))

    def test_pendiente_no_es_facturable(self):
        cobro = cobro_prueba(
            cuenta=self.efectivo, destinatario=self.dest, monto=D("65000.00"), estado="pendiente"
        )
        self.assertFalse(arca.facturable(cobro))

    def test_cobro_sin_destinatario_no_es_facturable(self):
        self.assertFalse(arca.facturable(self.cobro(destinatario=None)))


class TransferenciaTest(Fixture, BaseTransferencia):
    def test_pendiente_no_crea_factura_y_al_adjuntar_el_comprobante_crea_una(self):
        activar_facturacion(self.dest)
        cobro = self.alta()
        self.assertEqual(self.facturas().count(), 0)
        with self.captureOnCommitCallbacks(execute=True):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        factura = self.facturas().get()
        self.assertEqual(factura.cobro, cobro)
        self.emitir.assert_called_once_with(factura.pk)

    def test_adjuntar_sin_facturacion_no_crea_factura(self):
        cobro = self.alta()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        self.assertEqual(self.facturas().count(), 0)

    def test_un_pago_complementario_no_crea_comprobante_fiscal(self):
        activar_facturacion(self.dest)
        servicios.registrar_pago_complementario(
            usuario=self.admin,
            destinatario=self.dest,
            mes=date(2026, 10, 1),
            monto=D("10000.00"),
            metodo="transferencia",
            cuenta=self.cuenta,
        )
        self.assertEqual(self.facturas().count(), 0)
        # control: un cobro del mismo destinatario sí factura
        servicios.adjuntar_comprobante(self.alta(), imagen(), usuario=self.ana)
        self.assertEqual(self.facturas().count(), 1)


class FaltanItemsTest(Fixture, FaltanItemsBase, BaseEfectivo):
    def test_finalizado_sin_items_no_crea_factura_y_al_completar_los_items_crea_una(self):
        activar_facturacion(self.efectivo.destinatario)
        cobro = self.faltan(monto="50000.00")
        self.assertEqual(cobro.estado, "finalizado")
        self.assertEqual(self.facturas().count(), 0)
        with self.captureOnCommitCallbacks(execute=True):
            self.completar(cobro, [item(1, "40000.00"), item(2, "10000.00")])
        factura = self.facturas().get()
        self.assertEqual(factura.cobro_id, cobro.pk)
        self.emitir.assert_called_once_with(factura.pk)


class PosTest(Fixture, BasePos):
    def test_cuenta_de_terminal_de_un_destinatario_con_facturacion_crea_factura(self):
        activar_facturacion(self.cuenta.destinatario)
        cobro = self.pos()
        self.assertEqual(self.facturas().get().cobro, cobro)

    def test_cuenta_de_terminal_de_un_destinatario_sin_facturacion_no_crea_factura(self):
        self.pos()
        self.assertEqual(self.facturas().count(), 0)
