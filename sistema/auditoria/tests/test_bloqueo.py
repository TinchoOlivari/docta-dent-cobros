from datetime import datetime, timezone as tz
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.urls import reverse

from payment_instructions.models import User
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import desbloquear_usuario, historial_de
from sistema.core.tests import SistemaTestCase


def a(h, m, s):
    return datetime(2026, 10, 19, h, m, s, tzinfo=tz.utc)


class BloqueoTest(SistemaTestCase):
    def setUp(self):
        self.ana = User.objects.create_user(
            "ana", email="a@example.com", password="clave-123", role="operator"
        )
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="clave-123", role="administrator"
        )

    def intento(self, clave, hora):
        with patch("django.utils.timezone.now", return_value=hora):
            self.client.post(reverse("usuarios:login"), {"username": "ana", "password": clave})
            # la sesión vence 14 días después de `hora`: se lee con el mismo reloj fijo
            return "_auth_user_id" in self.client.session

    def fallar(self, n, hora=None):
        for _ in range(n):
            self.intento("mal", hora or a(9, 0, 0))

    def bloqueos(self):
        return Evento.objects.filter(tipo=Evento.Tipo.BLOQUEO).count()

    def test_cuatro_fallos_y_la_correcta_entra_y_reinicia_contador(self):
        self.fallar(4)
        self.assertTrue(self.intento("clave-123", a(9, 1, 0)))
        self.client.logout()
        self.fallar(4, a(9, 2, 0))
        self.assertTrue(self.intento("clave-123", a(9, 3, 0)))
        self.assertEqual(self.bloqueos(), 0)

    def test_cinco_fallos_bloquean_hasta_las_10_15_00(self):
        self.fallar(5, a(10, 0, 0))
        self.assertEqual(self.bloqueos(), 1)
        self.assertFalse(self.intento("clave-123", a(10, 14, 59)))
        e = Evento.objects.filter(tipo=Evento.Tipo.LOGIN_FALLIDO).first()
        self.assertEqual(e.detalle, "Usuario bloqueado")
        self.assertTrue(self.intento("clave-123", a(10, 15, 0)))
        self.assertEqual(self.bloqueos(), 1)

    def test_fallo_durante_el_bloqueo_no_lo_alarga(self):
        self.fallar(5, a(10, 0, 0))
        self.intento("mal", a(10, 10, 0))
        self.assertEqual(self.bloqueos(), 1)
        self.assertTrue(self.intento("clave-123", a(10, 15, 0)))

    def test_cuatro_fallos_no_bloquean(self):
        self.fallar(4, a(10, 0, 0))
        self.assertTrue(self.intento("clave-123", a(10, 0, 1)))

    def test_administrador_desbloquea_y_ana_entra(self):
        self.fallar(5, a(10, 0, 0))
        self.client.force_login(self.admin)
        with patch("django.utils.timezone.now", return_value=a(10, 5, 0)):
            r = self.client.post(reverse("usuarios:desbloquear", args=[self.ana.pk]))
        self.assertEqual(r.status_code, 302)
        d = Evento.objects.get(tipo=Evento.Tipo.DESBLOQUEO)
        self.assertEqual(d.usuario, self.admin)
        self.client.logout()
        self.assertTrue(self.intento("clave-123", a(10, 5, 1)))
        tipos = set(historial_de(self.ana).values_list("tipo", flat=True))
        self.assertTrue({"bloqueo", "desbloqueo"} <= tipos)

    def test_desbloquear_sin_bloqueo_falla(self):
        with self.assertRaises(ValidationError):
            desbloquear_usuario(self.ana, usuario=self.admin)

    @patch("django.utils.timezone.now", return_value=a(8, 0, 0))
    def test_permisos(self, _ahora):
        self.fallar(5)
        url = reverse("usuarios:desbloquear", args=[self.ana.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        pro = User.objects.create_user(
            "pro", email="p@example.com", password="clave-123", role="professional"
        )
        for u in (self.ana, pro):
            self.client.force_login(u)
            self.assertEqual(self.client.post(url).status_code, 403)
