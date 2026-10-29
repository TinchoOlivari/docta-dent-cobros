"""020 Fase A: Nuevo cobro (FR-002, FR-003, FR-004)."""

from django.urls import reverse

from sistema.cobros.tests.test_permisos_cobros import BasePermisos

NUEVO = reverse("cobros:nuevo")


class RegistrarOtroCobroTest(BasePermisos):
    def test_el_profesional_no_ve_el_detalle(self):
        c = self.cobro()
        self.client.force_login(self.drax)
        self.assertEqual(self.client.get(self.url("detalle", c)).status_code, 403)
