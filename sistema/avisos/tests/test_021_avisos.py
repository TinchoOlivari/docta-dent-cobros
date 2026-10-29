"""021 FR-045 y FR-046: Avisos."""

from django.urls import reverse

from payment_instructions.models import User
from sistema.avisos.servicios import margen
from sistema.avisos.tests import AvisosTestCase

URL = reverse("avisos:configuracion")
MENSAJE = "El margen debe ser de 0 % a 99 %."


class AvisosTest(AvisosTestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_user("adm", email="a@example.com", password="x", role="administrator")
        self.client.login(username="adm", password="x")

    def test_margen_en_los_limites_se_guarda(self):
        for valor in (0, 99):
            with self.subTest(valor=valor):
                r = self.client.post(URL, {"margen": valor})
                self.assertRedirects(r, URL)
                self.assertEqual(margen(), valor)

    def test_margen_fuera_de_rango_o_no_entero_se_rechaza(self):
        for valor in ("100", "-1", "1,5", "", "abc"):
            with self.subTest(valor=valor):
                r = self.client.post(URL, {"margen": valor})
                self.assertContains(r, MENSAJE)
                self.assertEqual(r.status_code, 200)

    def test_margen_invalido_no_cambia_el_guardado(self):
        self.client.post(URL, {"margen": "50"})
        self.client.post(URL, {"margen": "100"})
        self.assertEqual(margen(), 50)
