from decimal import Decimal

from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

UA = "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0"


class HistorialTest(SistemaTestCase):
    def setUp(self):
        self.maria = User.objects.create_user(
            "maria", email="m@example.com", password="x", role="administrator"
        )
        self.d = crear_destinatario(usuario=self.maria, nombre="Ana Test", tipo="profesional")
        self.cuenta = crear_cuenta(
            usuario=self.maria,
            tipo="transferencia",
            destinatario=self.d,
            alias="a.b",
            cbu="0000000000000000000001",
            tope_mensual=Decimal("500000.00"),
        )

    def url(self, entidad="destinatarios.cuenta", pk=None):
        return reverse("auditoria:historial", args=[entidad, pk or self.cuenta.pk])

    def editar_tope(self):
        return self.client.post(
            reverse("destinatarios:editar_cuenta", args=[self.cuenta.pk]),
            {
                "destinatario": self.d.pk,
                "alias": "a.b",
                "cbu": "0000000000000000000001",
                "tope_mensual": "600000,00",
                "tope_semanal": "0,00",
                "minimo_por_pago": "0,00",
                "estado": "activa",
            },
            HTTP_X_REAL_IP="190.0.0.1",
            HTTP_USER_AGENT=UA,
        )

    def test_us1_1_historial_muestra_usuario_ip_dispositivo_y_cambio(self):
        self.client.force_login(self.maria)
        self.editar_tope()
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.tope_mensual, Decimal("600000.00"))
        r = self.client.get(self.url())
        for texto in (
            "maria",
            "190.0.0.1",
            "Chrome/120.0",
            "Tope mensual",
            "$500.000,00",
            "$600.000,00",
        ):
            self.assertContains(r, texto)

    def test_sin_acciones_de_edicion_ni_borrado(self):
        from sistema.auditoria import urls

        self.assertLessEqual({p.name for p in urls.urlpatterns}, {"eventos", "historial"})

    def test_entidad_invalida_o_pk_inexistente_da_404(self):
        self.client.force_login(self.maria)
        self.assertEqual(self.client.get(self.url("auditoria.evento", 1)).status_code, 404)
        self.assertEqual(self.client.get(self.url("x.y", 1)).status_code, 404)
        self.assertEqual(self.client.get(self.url(pk=999999)).status_code, 404)

    def test_acceso_por_rol(self):
        op = User.objects.create_user("op", email="o@example.com", password="x", role="operator")
        pro = User.objects.create_user(
            "pr", email="p@example.com", password="x", role="professional"
        )
        url = self.url()
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/sistema/login/"))
        for usuario, esperado in ((self.maria, 200), (op, 403), (pro, 403)):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(url).status_code, esperado, usuario.role)
