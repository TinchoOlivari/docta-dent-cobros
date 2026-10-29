"""Permisos de 002/FR-009 sobre pagos complementarios: solo el Administrador (008 T034, T035)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse

from sistema.cobros.models import PagoComplementario
from sistema.cobros.tests.fabrica import pago_prueba
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

User = get_user_model()
LOGIN = reverse("usuarios:login")


class PermisosComplementarios(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user("admin", email="a@example.com", role="administrator")
        self.ana = User.objects.create_user("ana", email="n@example.com", role="operator")
        self.drax = User.objects.create_user("drax", email="d@example.com", role="professional")
        dest = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="jimena.roble.rana",
            tope_mensual=Decimal("600000.00"),
        )
        self.pago = pago_prueba(
            destinatario=dest,
            cuenta=cuenta,
            monto=Decimal("1000.00"),
            mes=date(2026, 10, 1),
            creado_por=self.admin,
        )
        self.pago.comprobante.save("c.png", ContentFile(b"x"), save=True)

    def urls(self):
        pk = self.pago.pk
        return [
            reverse("cobros:complementarios"),
            reverse("cobros:complementario_nuevo"),
            reverse("cobros:complementario_detalle", args=[pk]),
            reverse("cobros:complementario_editar", args=[pk]),
            reverse("cobros:complementario_anular", args=[pk]),
            reverse("cobros:complementario_comprobante", args=[pk]),
            reverse("auditoria:historial", args=["cobros.pagocomplementario", pk]),
        ]

    def estado(self):
        return list(PagoComplementario.objects.values_list("pk", "estado", "monto"))

    def test_sin_sesion_redirige_al_login(self):
        antes = self.estado()
        for url in self.urls():
            for metodo in (self.client.get, self.client.post):
                r = metodo(url)
                self.assertEqual(r.status_code, 302, url)
                self.assertTrue(r.url.startswith(LOGIN), url)
        self.assertEqual(self.estado(), antes)

    def test_operador_y_profesional_reciben_403_y_no_cambian_nada(self):
        antes = self.estado()
        for usuario in (self.ana, self.drax):
            self.client.force_login(usuario)
            for url in self.urls():
                for metodo in (self.client.get, self.client.post):
                    self.assertEqual(metodo(url).status_code, 403, (usuario.username, url))
        self.assertEqual(self.estado(), antes)

    def test_admin_accede_a_todas_las_pantallas(self):
        self.client.force_login(self.admin)
        anular = reverse("cobros:complementario_anular", args=[self.pago.pk])
        for url in self.urls():
            esperado = 405 if url == anular else 200  # anular es solo POST
            self.assertEqual(self.client.get(url).status_code, esperado, url)
        self.assertEqual(self.client.post(anular).status_code, 302)  # sin motivo: vuelve al detalle
