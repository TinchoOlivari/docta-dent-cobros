from django.test import Client
from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase


class LoginTest(SistemaTestCase):
    def crear(self, nombre, rol, **extra):
        return User.objects.create_user(
            nombre, email=f"{nombre}@example.com", password="clave-123", role=rol, **extra
        )

    def entrar(self, nombre):
        return self.client.post(
            reverse("usuarios:login"), {"username": nombre, "password": "clave-123"}
        )

    def test_cada_rol_llega_a_su_pantalla_con_dos_redirects(self):
        destinos = {
            "administrator": "/sistema/panel/",
            "operator": "/sistema/operador/",
            "professional": "/sistema/profesional/",
        }
        for rol, destino in destinos.items():
            with self.subTest(rol=rol):
                self.crear(f"u_{rol}", rol)
                r = Client().post(
                    reverse("usuarios:login"),
                    {"username": f"u_{rol}", "password": "clave-123"},
                    follow=True,
                )
                self.assertEqual(r.redirect_chain, [("/sistema/", 302), (destino, 302)])
                self.assertEqual(r.status_code, 200)

    def test_desactivado_con_clave_correcta_no_entra(self):
        self.crear("baja", "operator", is_active=False)
        r = self.entrar("baja")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_clave_incorrecta_no_entra(self):
        self.crear("ana", "operator")
        r = self.client.post(reverse("usuarios:login"), {"username": "ana", "password": "mal"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_salir_por_post_cierra_la_sesion(self):
        self.crear("ana", "operator")
        self.entrar("ana")
        self.assertIn("_auth_user_id", self.client.session)
        r = self.client.post(reverse("usuarios:logout"))
        self.assertRedirects(r, reverse("usuarios:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_salir_por_get_no_cierra_la_sesion(self):
        self.crear("ana", "operator")
        self.entrar("ana")
        r = self.client.get(reverse("usuarios:logout"))
        self.assertEqual(r.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_rol_desconocido_recibe_403_en_inicio(self):
        self.crear("raro", "otro")
        self.entrar("raro")
        self.assertEqual(self.client.get(reverse("usuarios:inicio")).status_code, 403)
