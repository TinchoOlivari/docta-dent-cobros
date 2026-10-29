"""018 Fase A: Nuevo cobro (FR-007, FR-012 a FR-014)."""

from django.urls import reverse

from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import con_elegidas
from sistema.cobros.tests.test_efectivo import BaseEfectivo

NUEVO = reverse("cobros:nuevo")


class NuevoCobroTest(BaseEfectivo):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)

    def html(self, url):
        return self.client.get(url).content.decode()

    def test_post_en_efectivo_ignora_observaciones(self):
        self.client.post(
            f"{NUEVO}?paciente=501",
            con_elegidas({"metodo": "efectivo", "monto_1": "100,00", "observaciones": "x"}),
        )
        self.assertEqual(Cobro.objects.get().observaciones, "")

    def test_sin_metodo_el_error_sale_bajo_el_grupo(self):
        r = self.client.post(f"{NUEVO}?paciente=501", con_elegidas({"monto_1": "100,00"}))
        self.assertContains(r, 'id="id_metodo-error"')
        self.assertEqual(Cobro.objects.count(), 0)
