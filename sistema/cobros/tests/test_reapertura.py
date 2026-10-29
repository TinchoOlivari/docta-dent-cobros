from decimal import Decimal
from unittest import mock

from django.urls import reverse
from django.core.exceptions import PermissionDenied, ValidationError

from sistema.arca.servicios import FaltaDecision
from sistema.asignacion.servicios import SinCuentaDisponible, usado_mes
from sistema.auditoria.models import Evento
from sistema.cobros import servicios
from sistema.dentalink import tasks
from sistema.cobros.models import Cobro, ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_comprobantes_n import BaseComprobantes
from sistema.cobros.tests.test_efectivo import AHORA, DELAY, item
from sistema.destinatarios.models import Cuenta

D = Decimal


class ReaperturaTest(BaseComprobantes):
    def finalizado(self):
        cobro = self.alta100()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        cobro.refresh_from_db()
        return cobro

    def reabrir(self, cobro, usuario=None, motivo="Falta transferir $40.000,00", **kw):
        kw.setdefault("estado_visto", "finalizado")
        return servicios.reabrir_cobro(cobro, usuario=usuario or self.admin, motivo=motivo, **kw)

    def test_reabrir_deja_pendiente_y_marca_el_item_enviado(self):
        cobro = self.finalizado()
        ItemCobro.objects.filter(cobro=cobro, numero=1).update(estado_envio="enviado")
        self.reabrir(cobro)
        cobro.refresh_from_db()
        self.assertEqual(
            (cobro.estado, cobro.cuenta, cobro.monto), ("pendiente", self.cuenta, D("100000.00"))
        )
        self.assertEqual(usado_mes(self.cuenta, AHORA), D("100000.00"))
        self.assertTrue(cobro.comprobante)
        estados = dict(cobro.items.values_list("numero", "estado_envio"))
        self.assertEqual(estados, {1: "requiere_ajuste", 2: "pendiente"})
        evento = Evento.objects.filter(tipo="cambio", entidad="cobros.cobro").latest("pk")
        self.assertIn(
            {"campo": "estado", "anterior": "finalizado", "nuevo": "pendiente"}, evento.cambios
        )
        self.assertEqual(evento.detalle, "Falta transferir $40.000,00")

    def test_al_adjuntar_de_nuevo_finaliza_y_solo_envia_el_pendiente(self):
        self.facturar()
        cobro = self.alta100()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        ItemCobro.objects.filter(cobro=cobro, numero=1).update(estado_envio="enviado")
        self.reabrir(cobro)
        with mock.patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")
        self.assertEqual(cobro.otros_comprobantes.count(), 1)
        delay.assert_called_once_with(cobro.pk)
        self.assertEqual(self.facturas(cobro), 1)
        # la tarea real manda solo el ítem 2; el 1 (requiere_ajuste) no sale
        with mock.patch("sistema.dentalink.tasks.enviar_item") as enviar:
            tasks.enviar_cobro(cobro.pk)
        enviar.assert_called_once_with(cobro.items.get(numero=2).pk)
        self.assertEqual(cobro.items.get(numero=1).estado_envio, "requiere_ajuste")

    def test_reabrir_un_cobro_en_el_tope_no_cambia_el_cupo(self):
        cobro = cobro_prueba(
            cuenta=self.cuenta,
            monto=D("300000.00"),
            estado="finalizado",
            fecha=AHORA,
            excedente=False,
        )
        self.reabrir(cobro)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "pendiente")
        self.assertEqual(usado_mes(self.cuenta, AHORA), D("300000.00"))
        self.assertFalse(cobro.excedente)
        with self.assertRaises(SinCuentaDisponible):
            self.alta("0.01", [item(1, "0.01")])

    def test_rechazos_no_cambian_estado_ni_eventos(self):
        cobro = self.finalizado()
        efectivo = cobro_prueba(
            cuenta=Cuenta.objects.get_or_create(
                tipo="efectivo", defaults={"destinatario": self.dest}
            )[0],
            monto=D("10.00"),
            metodo="efectivo",
            estado="finalizado",
            fecha=AHORA,
        )
        pos = cobro_prueba(
            cuenta=self.cuenta, monto=D("10.00"), metodo="debito", estado="finalizado", fecha=AHORA
        )
        pendiente = cobro_prueba(
            cuenta=self.cuenta, monto=D("10.00"), estado="pendiente", fecha=AHORA
        )
        cancelado = cobro_prueba(
            cuenta=self.cuenta, monto=D("20.00"), estado="cancelado", fecha=AHORA
        )
        eventos = Evento.objects.count()
        casos = [
            (cobro, {"usuario": self.ana}, PermissionDenied, None),
            (cobro, {"motivo": ""}, ValidationError, "Escribí el motivo de la reapertura."),
            (cobro, {"motivo": "   "}, ValidationError, "Escribí el motivo de la reapertura."),
            (efectivo, {}, ValidationError, "Solo un cobro por transferencia se puede reabrir."),
            (pos, {}, ValidationError, "Solo un cobro por transferencia se puede reabrir."),
            (
                pendiente,
                {"estado_visto": "pendiente"},
                ValidationError,
                "El cobro ya está Pendiente.",
            ),
            (
                cancelado,
                {"estado_visto": "cancelado"},
                ValidationError,
                "El cobro ya está Cancelado.",
            ),
            (
                cobro,
                {"estado_visto": "pendiente"},
                ValidationError,
                "El cobro cambió a Finalizado.",
            ),
        ]
        for objetivo, kw, error, mensaje in casos:
            estado = Cobro.objects.get(pk=objetivo.pk).estado
            with self.subTest(objetivo=objetivo.pk, kw=kw):
                with self.assertRaises(error) as ctx:
                    self.reabrir(objetivo, **kw)
                if mensaje:
                    self.assertIn(mensaje, str(ctx.exception))
                self.assertEqual(Cobro.objects.get(pk=objetivo.pk).estado, estado)
        self.assertEqual(Evento.objects.count(), eventos)

    def test_cobro_reabierto_con_factura_no_se_factura_de_nuevo(self):
        self.facturar()
        cobro = self.finalizado()
        self.assertEqual(self.facturas(cobro), 1)
        cobro.comprobantes_fiscales.update(vigente=False)  # una nota de crédito la anuló
        self.reabrir(cobro)
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        self.assertEqual(cobro.comprobantes_fiscales.filter(tipo="factura").count(), 1)

    def test_falta_estado_visto_da_su_propio_mensaje(self):
        cobro = self.finalizado()
        with self.assertRaisesMessage(
            ValidationError, "Recargá la página para ver el estado actual."
        ):
            self.reabrir(cobro, estado_visto="")
        self.assertEqual(Cobro.objects.get(pk=cobro.pk).estado, "finalizado")


class FacturaEmitidaCancelarTest(BaseComprobantes):
    """Con factura emitida cancela solo el administrador (decisión de Martin, 2026-10-22)."""

    def setUp(self):
        super().setUp()
        self.facturar()
        self.cobro_r = self.alta100()
        servicios.adjuntar_comprobante(self.cobro_r, imagen(), usuario=self.ana)
        self.cobro_r.comprobantes_fiscales.update(
            estado="emitido",
            cbte_tipo=6,
            punto_venta=3,
            numero=42,
            cae="70000000000001",
            cae_vence=AHORA.date(),
            fecha=AHORA.date(),
            neto=D("82644.63"),
            iva=D("17355.37"),
            total=D("100000.00"),
        )
        servicios.reabrir_cobro(
            self.cobro_r, usuario=self.admin, motivo="Falta", estado_visto="finalizado"
        )
        self.url = reverse("cobros:cancelar", args=[self.cobro_r.pk])
        self.datos = {"motivo": "datos_erroneos", "estado_visto": "pendiente"}

    def test_operador_no_ve_el_form_y_su_post_se_rechaza(self):
        self.client.force_login(self.ana)
        r = self.client.get(reverse("cobros:detalle", args=[self.cobro_r.pk]))
        self.assertNotContains(r, self.url)
        self.assertEqual(self.client.post(self.url, self.datos).status_code, 403)
        self.assertEqual(Cobro.objects.get(pk=self.cobro_r.pk).estado, "pendiente")
        with self.assertRaises(PermissionDenied):
            servicios.cancelar_cobro(self.cobro_r, usuario=self.ana, **self.datos)

    def test_administrador_ve_el_form_y_recibe_la_pregunta_de_nota_de_credito(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("cobros:detalle", args=[self.cobro_r.pk]))
        self.assertContains(r, self.url)
        self.assertContains(r, "¿Emitir nota de crédito por $100.000,00?")
        with self.assertRaises(FaltaDecision):
            servicios.cancelar_cobro(self.cobro_r, usuario=self.admin, **self.datos)
        servicios.cancelar_cobro(self.cobro_r, usuario=self.admin, nota_credito=False, **self.datos)
        self.assertEqual(Cobro.objects.get(pk=self.cobro_r.pk).estado, "cancelado")
