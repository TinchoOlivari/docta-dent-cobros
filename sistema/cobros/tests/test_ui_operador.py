from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.cobros import servicios
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_comprobantes_n import BaseComprobantes
from sistema.cobros.tests.test_transferencia import BaseTransferencia
from sistema.destinatarios.models import Cuenta

CBU = "0000000000000000000001"


class UiOperadorTest(BaseTransferencia):
    def setUp(self):
        super().setUp()
        Cuenta.objects.filter(pk=self.cuenta.pk).update(cbu=CBU)
        self.cobro = self.alta()

    def test_detalle_de_otro_operador_es_404(self):
        otro = get_user_model().objects.create_user("otra", email="o@example.com", role="operator")
        self.client.force_login(otro)
        r = self.client.get(reverse("cobros:detalle", args=[self.cobro.pk]))
        self.assertEqual(r.status_code, 404)


class NuevoCobroUiTest(BaseTransferencia):
    def setUp(self):
        super().setUp()
        self.cobro = self.alta()

    def nuevo(self, **q):
        self.client.force_login(self.ana)
        q = {"paciente": "501", **q}
        return self.client.get(reverse("cobros:nuevo"), q)

    def test_nuevo_y_detalle_del_operador_no_tienen_barras_de_tope(self):
        self.assertNotContains(self.nuevo(), 'role="progressbar"')
        r = self.client.get(reverse("cobros:detalle", args=[self.cobro.pk]))
        self.assertNotContains(r, 'role="progressbar"')


class DetalleFinalizadoUiTest(BaseComprobantes):
    def setUp(self):
        super().setUp()
        self.fin = self.alta100()
        servicios.adjuntar_comprobante(self.fin, imagen(), usuario=self.ana)
        self.url = reverse("cobros:detalle", args=[self.fin.pk])

    def test_operador_no_ve_reabrir_ni_adjuntar(self):
        self.client.force_login(self.ana)
        r = self.client.get(self.url)
        self.assertNotContains(r, reverse("cobros:reabrir", args=[self.fin.pk]))
        self.assertNotContains(r, reverse("cobros:adjuntar", args=[self.fin.pk]))
        self.assertNotContains(r, "Reabrir")
        self.assertNotContains(r, ">Envío<")

    def test_efectivo_finalizado_del_operador_no_muestra_acciones_ni_ayuda(self):
        cobro = cobro_prueba(
            cuenta=self.cuenta,
            metodo="efectivo",
            estado="finalizado",
            creado_por=self.ana,
            monto="1000.00",
        )
        self.client.force_login(self.ana)
        r = self.client.get(reverse("cobros:detalle", args=[cobro.pk]))
        for texto in ("Reabrir", "Adjuntar", "Cuenta destino", "Todavía no hay comprobantes"):
            self.assertNotContains(r, texto)
