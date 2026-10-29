from django.urls import reverse

from payment_instructions.models import User
from sistema.avisos.models import ConfiguracionAvisos, DireccionAviso
from sistema.avisos.servicios import agregar_direccion, margen
from sistema.avisos.tests import AvisosTestCase


class PermisosTests(AvisosTestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_user(
            "admin", email="admin@example.com", password="x", role="administrator"
        )
        User.objects.create_user("ana", email="ana@example.com", password="x", role="operator")
        User.objects.create_user(
            "drax", email="drax@example.com", password="x", role="professional"
        )
        self.d = agregar_direccion(usuario=None, email="duenio@clinica.test")
        self.urls = [
            reverse("avisos:configuracion"),
            reverse("avisos:agregar"),
            reverse("avisos:borrar", args=[self.d.pk]),
        ]

    def llamadas(self):
        for url in self.urls:
            yield self.client.get(url)
            yield self.client.post(url, {"margen": "50", "email": "otro@clinica.test"})

    def test_sin_sesion_redirige_al_login(self):
        for r in self.llamadas():
            self.assertEqual(r.status_code, 302)
            self.assertIn(reverse("usuarios:login"), r["Location"])

    def test_operador_y_profesional_reciben_403_y_no_cambia_nada(self):
        for usuario in ("ana", "drax"):
            self.client.login(username=usuario, password="x")
            for r in self.llamadas():
                self.assertEqual(r.status_code, 403, usuario)
            self.client.logout()
        self.assertEqual(
            list(DireccionAviso.objects.values_list("email", flat=True)), ["duenio@clinica.test"]
        )
        self.assertEqual(margen(), 10)
        self.assertEqual(ConfiguracionAvisos.objects.count(), 0)

    def test_administrador_entra(self):
        self.client.login(username="admin", password="x")
        self.assertEqual(self.client.get(self.urls[0]).status_code, 200)
