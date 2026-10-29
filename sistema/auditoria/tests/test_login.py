from django.urls import reverse

from payment_instructions.models import User
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import historial_de
from sistema.core.tests import SistemaTestCase

UA = "Mozilla/5.0 Firefox/130.0"


class LoginEventosTest(SistemaTestCase):
    def setUp(self):
        self.ana = User.objects.create_user(
            "ana", email="a@example.com", password="clave-123", role="operator"
        )

    def post(self, usuario, clave):
        return self.client.post(
            reverse("usuarios:login"),
            {"username": usuario, "password": clave},
            REMOTE_ADDR="181.0.0.7",
            HTTP_USER_AGENT=UA,
        )

    def test_login_fallido_registra_usuario_ip_dispositivo_sin_contrasena(self):
        self.post("ana", "secreto-mal")
        e = Evento.objects.get(tipo=Evento.Tipo.LOGIN_FALLIDO)
        self.assertEqual(e.usuario_intentado, "ana")
        self.assertEqual(e.usuario, self.ana)
        self.assertEqual(e.entidad_id, self.ana.pk)
        self.assertEqual(e.ip, "181.0.0.7")
        self.assertIn("Firefox/130.0", e.dispositivo)
        self.assertNotIn("secreto-mal", str(vars(e)))

    def test_login_fallido_de_usuario_inexistente(self):
        self.post("pepe", "x")
        e = Evento.objects.get(tipo=Evento.Tipo.LOGIN_FALLIDO)
        self.assertEqual(e.usuario_intentado, "pepe")
        self.assertIsNone(e.usuario)
        self.assertEqual(e.entidad, "")
        self.assertIsNone(e.entidad_id)

    def test_salir_registra_cierre_de_sesion(self):
        self.post("ana", "clave-123")
        self.client.post(reverse("usuarios:logout"))
        e = Evento.objects.get(tipo=Evento.Tipo.CIERRE_SESION)
        self.assertEqual(e.usuario, self.ana)

    def test_tres_fallos_y_un_inicio_en_el_historial(self):
        for _ in range(3):
            self.post("ana", "mal")
        self.post("ana", "clave-123")
        tipos = sorted(historial_de(self.ana).values_list("tipo", flat=True))
        self.assertEqual(tipos.count("inicio_sesion"), 1)
        self.assertEqual(tipos.count("login_fallido"), 3)
