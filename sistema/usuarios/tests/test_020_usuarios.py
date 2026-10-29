from datetime import datetime, timezone as tz
from unittest.mock import patch

from django.urls import reverse

from payment_instructions.models import User
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario


def a(h, m, s):
    return datetime(2026, 10, 19, h, m, s, tzinfo=tz.utc)


class LoginBloqueadoTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_user(
            "ana", email="a@example.com", password="clave-123", role="operator"
        )

    def intento(self, usuario, clave, hora):
        with patch("django.utils.timezone.now", return_value=hora):
            r = self.client.post(
                reverse("usuarios:login"), {"username": usuario, "password": clave}
            )
            return r, "_auth_user_id" in self.client.session

    def bloquear(self):
        for _ in range(5):
            self.intento("ana", "mal", a(10, 0, 0))

    def test_bloqueado_dice_cuenta_bloqueada_con_clave_correcta_e_incorrecta(self):
        self.bloquear()
        for clave in ("clave-123", "mal"):
            with self.subTest(clave=clave):
                r, sesion = self.intento("ana", clave, a(10, 14, 59))
                self.assertContains(r, "Tu cuenta está bloqueada.")
                self.assertFalse(sesion)

    def test_a_las_10_15_00_entra(self):
        self.bloquear()
        self.assertTrue(self.intento("ana", "clave-123", a(10, 15, 0))[1])

    def test_intento_bloqueado_no_suma_fallos(self):
        from sistema.auditoria.servicios import fallos_seguidos

        self.bloquear()
        antes = fallos_seguidos(User.objects.get(username="ana"))
        self.intento("ana", "mal", a(10, 5, 0))
        self.assertEqual(fallos_seguidos(User.objects.get(username="ana")), antes)

    def test_usuario_inexistente_da_mensaje_comun(self):
        r, _ = self.intento("nadie", "x", a(10, 0, 0))
        self.assertNotContains(r, "bloqueada")


class AltaUsuarioTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        admin = User.objects.create_user(
            "adm", email="adm@example.com", password="clave-123", role="administrator"
        )
        self.client.force_login(admin)
        self.url = reverse("usuarios:nuevo")
        self.datos = {
            "username": "op4",
            "password1": "Clave-segura-77",
            "password2": "Clave-segura-77",
        }

    def test_sin_rol_no_crea_y_pide_rol(self):
        r = self.client.post(self.url, {**self.datos, "role": ""})
        self.assertContains(r, "Elegí un rol.")
        self.assertFalse(User.objects.filter(username="op4").exists())

    def test_con_rol_operador_crea(self):
        self.client.post(self.url, {**self.datos, "role": "operator"})
        self.assertEqual(User.objects.get(username="op4").role, "operator")

    def test_contrasenas_distintas(self):
        r = self.client.post(self.url, {**self.datos, "password2": "otra", "role": "operator"})
        self.assertContains(r, "Las dos contraseñas no coinciden.")

    def test_cambio_de_contrasena_distintas(self):
        u = User.objects.get(username="adm")
        r = self.client.post(
            reverse("usuarios:contrasena", args=[u.pk]),
            {"new_password1": "Clave-segura-77", "new_password2": "otra"},
        )
        self.assertContains(r, "Las dos contraseñas no coinciden.")


class ProfesionalSinVinculoTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.prof = User.objects.create_user(
            "dra", email="d@example.com", password="x", role="professional"
        )
        self.dest = crear_destinatario(usuario=None, nombre="Ejemplo", tipo="profesional")
        self.dest.usuario_profesional = self.prof
        self.dest.save()
        cuenta = crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=self.dest,
            alias="ejemplo.alias", cbu="0000000000000000000001", tope_mensual="9000000.00",
        )  # fmt: skip
        cobro_prueba(cuenta=cuenta, destinatario=self.dest, monto="1300000.99", estado="finalizado")
        self.client.force_login(self.prof)

    def test_sin_vinculo_solo_muestra_recibido(self):
        r = self.client.get(reverse("usuarios:profesional"))
        self.assertContains(r, "$1.300.000,99")
