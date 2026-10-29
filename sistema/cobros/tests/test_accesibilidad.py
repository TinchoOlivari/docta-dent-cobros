from django.core.exceptions import ValidationError
from django.urls import reverse

from sistema.cobros import servicios

from sistema.cobros.tests.test_edicion import BaseEdicion


class DetalleAccionesTest(BaseEdicion):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def html(self, cobro):
        return self.client.get(reverse("cobros:detalle", args=[cobro.pk])).content.decode()

    def test_cancelar_sin_motivo_se_rechaza_en_el_servidor(self):
        cobro = self.alta()
        for motivo in ("", "   "):
            with self.subTest(motivo=motivo):
                with self.assertRaisesMessage(ValidationError, "Elegí un motivo de cancelación."):
                    servicios.cancelar_cobro(
                        cobro, usuario=self.admin, motivo=motivo, estado_visto="pendiente"
                    )
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "pendiente")
        servicios.cancelar_cobro(
            cobro, usuario=self.admin, motivo="datos_erroneos", estado_visto="pendiente"
        )
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "cancelado")
