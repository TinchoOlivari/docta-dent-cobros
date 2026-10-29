from django.urls import resolve, reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.usuarios import roles

CLAVE = "clave-de-siempre-1"


class RutasTests(SistemaTestCase):
    def setUp(self):
        super().setUp()

    def _usuario(self, nombre, rol, **extra):
        u = User.objects.create(username=nombre, role=rol, **extra)
        u.set_password(CLAVE)
        u.save()
        return u

    def test_raiz_redirige_a_sistema(self):
        r = self.client.get("/")
        self.assertRedirects(r, "/sistema/", fetch_redirect_response=False)

    def test_rutas_viejas_redirigen_a_raiz(self):
        for ruta in ("/admin/", "/login/", "/crear-pago/", "/pago-comun/"):
            with self.subTest(ruta=ruta):
                self.assertRedirects(self.client.get(ruta), "/", fetch_redirect_response=False)
                self.assertEqual(resolve(ruta).func.__name__, "redirect_to_home")

    def test_url_inexistente_bajo_sistema_es_404_con_la_pagina_propia(self):
        self._usuario("adm", roles.ADMIN)
        self.client.login(username="adm", password=CLAVE)
        for ruta in ("/sistema/cosa-inexistente/", "/sistema/cobros/999999/"):
            with self.subTest(ruta=ruta):
                r = self.client.get(ruta)
                self.assertEqual(r.status_code, 404)

    def test_fuera_de_sistema_la_ruta_vieja_redirige_a_raiz(self):
        self.assertRedirects(self.client.get("/pagos/viejo/"), "/", fetch_redirect_response=False)

    def test_health_responde_200(self):
        self.assertEqual(self.client.get("/health/").status_code, 200)

    def test_sin_sesion_va_al_login_nuevo(self):
        r = self.client.get(reverse("usuarios:operador"))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith(reverse("usuarios:login")))

    def test_ana_entra_con_su_contrasena_de_siempre(self):
        self._usuario("ana", roles.OPERATOR)
        r = self.client.post(
            reverse("usuarios:login"),
            {"username": "ana", "password": CLAVE},
            follow=True,
        )
        self.assertEqual(r.redirect_chain[-1][0], reverse("usuarios:operador"))
        self.assertEqual(r.status_code, 200)

    def test_superusuario_con_rol_operador_no_entra_al_panel(self):
        self._usuario("super", roles.OPERATOR, is_superuser=True, is_staff=True)
        self.client.login(username="super", password=CLAVE)
        r = self.client.get(reverse("usuarios:panel"))
        self.assertEqual(r.status_code, 403)
        self.assertRedirects(self.client.get("/admin/"), "/", fetch_redirect_response=False)

    def test_admin_no_responde_a_ningun_rol(self):
        for rol in (roles.ADMIN, roles.OPERATOR, roles.PROFESSIONAL):
            with self.subTest(rol=rol):
                self._usuario(f"s-{rol}", rol, is_superuser=True, is_staff=True)
                self.client.login(username=f"s-{rol}", password=CLAVE)
                for ruta in ("/admin/", "/admin/login/", "/admin/payment_instructions/user/"):
                    self.assertRedirects(self.client.get(ruta), "/", fetch_redirect_response=False)

    def test_cada_rol_solo_entra_a_sus_pantallas(self):
        solo_admin = ("usuarios:panel", "usuarios:lista", "dentalink:configuracion")
        # (rol, pantallas prohibidas)
        casos = {
            roles.ADMIN: ("usuarios:profesional",),
            roles.OPERATOR: (*solo_admin, "usuarios:profesional"),
            roles.PROFESSIONAL: (*solo_admin, "usuarios:operador"),
        }
        for rol, prohibidas in casos.items():
            self._usuario(f"u-{rol}", rol)
            self.client.login(username=f"u-{rol}", password=CLAVE)
            for nombre in prohibidas:
                with self.subTest(rol=rol, pantalla=nombre):
                    self.assertEqual(self.client.get(reverse(nombre)).status_code, 403)
            self.client.logout()
