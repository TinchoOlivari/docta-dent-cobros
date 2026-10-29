from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db.models import ProtectedError

from sistema.arca.tests.fabricas import emisor_prueba
from sistema.asignacion.servicios import usado_mes
from sistema.auditoria.models import Evento
from sistema.cobros import servicios
from sistema.cobros.models import Cobro, ComprobanteCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_efectivo import AHORA, DELAY, TRATAMIENTOS, item
from sistema.cobros.tests.test_transferencia import BaseTransferencia
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import cambiar_estado_cuenta, crear_cuenta

D = Decimal


class BaseComprobantes(BaseTransferencia):
    """Una sola cuenta con cupo: "alias.uno" con tope de $300.000,00."""

    def setUp(self):
        super().setUp()
        cambiar_estado_cuenta(self.cuenta, Cuenta.Estado.ARCHIVADA, usuario=self.admin)
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="alias.uno",
            tope_mensual=D("300000.00"),
        )
        p = mock.patch(
            TRATAMIENTOS,
            return_value=[tratamiento([prestacion(1, "60000", "0"), prestacion(2, "40000", "0")])],
        )
        p.start()
        self.addCleanup(p.stop)

    def alta100(self):
        return self.alta("100000.00", [item(1, "60000.00"), item(2, "40000.00")])

    def facturar(self):
        Destinatario.objects.filter(pk=self.dest.pk).update(
            emisor=emisor_prueba(), facturacion_activa=True
        )

    def facturas(self, cobro):
        return cobro.comprobantes_fiscales.filter(tipo="factura", vigente=True).count()


class ComprobantesTest(BaseComprobantes):
    def test_un_comprobante_finaliza_sin_otros_y_el_cupo_no_cambia(self):
        self.facturar()
        cobro = self.alta100()
        self.assertEqual(usado_mes(self.cuenta, AHORA), D("100000.00"))
        with (
            mock.patch(DELAY) as delay,
            mock.patch("sistema.arca.tasks.emitir.delay"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")
        self.assertEqual(cobro.monto, D("100000.00"))
        self.assertEqual(cobro.otros_comprobantes.count(), 0)
        self.assertEqual(usado_mes(self.cuenta, AHORA), D("100000.00"))
        self.assertEqual(delay.call_count, 1)
        self.assertEqual(self.facturas(cobro), 1)

    def test_tres_comprobantes_el_admin_suma_sin_tocar_nada(self):
        self.facturar()
        cobro = self.alta100()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        with mock.patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            servicios.adjuntar_comprobante(
                cobro, imagen(), usuario=self.admin, estado_visto="finalizado"
            )
            servicios.adjuntar_comprobante(
                cobro, imagen(), usuario=self.admin, estado_visto="finalizado"
            )
        cobro.refresh_from_db()
        self.assertEqual(cobro.otros_comprobantes.count(), 2)
        self.assertEqual(cobro.estado, "finalizado")
        self.assertEqual(cobro.monto, D("100000.00"))
        self.assertEqual(usado_mes(self.cuenta, AHORA), D("100000.00"))
        delay.assert_not_called()
        self.assertEqual(self.facturas(cobro), 1)

    def test_operador_no_suma_a_un_finalizado(self):
        cobro = self.alta100()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        with self.assertRaisesMessage(ValidationError, "El cobro ya está Finalizado."):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        self.assertEqual(cobro.otros_comprobantes.count(), 0)

    def test_solo_transferencia_admite_mas_comprobantes(self):
        for metodo in ("efectivo", "debito"):
            cobro = cobro_prueba(
                cuenta=Cuenta.objects.get_or_create(
                    tipo="efectivo", defaults={"destinatario": self.dest}
                )[0]
                if metodo == "efectivo"
                else self.cuenta,
                monto=D("100.00"),
                estado="finalizado",
                metodo=metodo,
                fecha=AHORA,
            )
            with self.assertRaisesMessage(
                ValidationError, "Solo un cobro por transferencia admite más comprobantes."
            ):
                servicios.adjuntar_comprobante(
                    cobro, imagen(), usuario=self.admin, estado_visto="finalizado"
                )
            self.assertEqual(cobro.otros_comprobantes.count(), 0)

    def test_cancelado_no_admite_comprobantes(self):
        cobro = self.alta100()
        self.cancelar(cobro)
        with self.assertRaisesMessage(ValidationError, "El cobro ya está Cancelado."):
            servicios.adjuntar_comprobante(
                cobro, imagen(), usuario=self.admin, estado_visto="cancelado"
            )

    def test_limite_de_tamano_en_el_segundo_comprobante(self):
        cobro = self.alta100()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        justo = imagen()
        justo.size = 5 * 1024 * 1024
        servicios.adjuntar_comprobante(cobro, justo, usuario=self.admin, estado_visto="finalizado")
        self.assertEqual(cobro.otros_comprobantes.count(), 1)
        archivos = default_storage.listdir("comprobantes")  # snapshot de existencia
        grande = imagen()
        grande.size = 5 * 1024 * 1024 + 1
        with self.assertRaisesMessage(ValidationError, "El comprobante pesa más de 5 MB."):
            servicios.adjuntar_comprobante(
                cobro, grande, usuario=self.admin, estado_visto="finalizado"
            )
        self.assertEqual(cobro.otros_comprobantes.count(), 1)
        self.assertEqual(default_storage.listdir("comprobantes"), archivos)

    def test_historial_del_alta_y_del_cambio_del_cobro(self):
        cobro = self.alta100()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        servicios.adjuntar_comprobante(
            cobro, imagen(), usuario=self.admin, estado_visto="finalizado"
        )
        otro = cobro.otros_comprobantes.get()
        self.assertTrue(
            Evento.objects.filter(tipo="alta", entidad="cobros.comprobantecobro").exists()
        )
        self.assertTrue(
            Evento.objects.filter(
                tipo="cambio", entidad="cobros.cobro", detalle="Comprobante agregado."
            ).exists()
        )
        self.assertEqual(otro.creado_por, self.admin)

    def test_un_comprobante_no_se_borra(self):
        cobro = self.alta100()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        servicios.adjuntar_comprobante(
            cobro, imagen(), usuario=self.admin, estado_visto="finalizado"
        )
        with self.assertRaises(ProtectedError):
            cobro.otros_comprobantes.get().delete()

    def test_si_falla_el_alta_del_segundo_se_borra_su_archivo(self):
        cobro = self.alta100()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        antes = default_storage.listdir("comprobantes")
        with mock.patch.object(servicios, "registrar_alta", side_effect=RuntimeError("falla")):
            with self.assertRaises(RuntimeError):
                servicios.adjuntar_comprobante(
                    cobro, imagen(), usuario=self.admin, estado_visto="finalizado"
                )
        self.assertEqual(ComprobanteCobro.objects.count(), 0)
        self.assertEqual(
            Cobro.objects.get(pk=cobro.pk).comprobante.name.split("/")[0], "comprobantes"
        )
        self.assertEqual(default_storage.listdir("comprobantes"), antes)

    def test_pagina_vieja_del_admin_no_adjunta(self):
        cobro = self.alta100()  # el admin vio Pendiente; ana ya lo finalizó
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        with self.assertRaisesMessage(
            ValidationError, "El cobro ya está Finalizado. No hace falta otro comprobante."
        ):
            servicios.adjuntar_comprobante(
                cobro, imagen(), usuario=self.admin, estado_visto="pendiente"
            )
        self.assertEqual(cobro.otros_comprobantes.count(), 0)

    def test_admin_sin_estado_visto_no_adjunta(self):
        cobro = self.alta100()
        with self.assertRaisesMessage(
            ValidationError, "Recargá la página para ver el estado actual."
        ):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.admin)
        self.assertEqual(Cobro.objects.get(pk=cobro.pk).estado, "pendiente")

    def test_admin_con_finalizado_visto_como_finalizado_adjunta(self):
        cobro = self.alta100()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        servicios.adjuntar_comprobante(
            cobro, imagen(), usuario=self.admin, estado_visto="finalizado"
        )
        self.assertEqual(cobro.otros_comprobantes.count(), 1)
