from decimal import Decimal

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory

from payment_instructions.models import User
from sistema.core.permisos import puede_cobrar, solo_administrador, solo_profesional
from sistema.core.templatetags.pesos import pesos
from sistema.core.tests import SistemaTestCase


@solo_administrador
def _vista(request):
    return HttpResponse("ok")


@puede_cobrar
def _vista_cobrar(request):
    return HttpResponse("ok")


@solo_profesional
def _vista_profesional(request):
    return HttpResponse("ok")


class PesosTest(SistemaTestCase):
    def test_pesos_con_centavos_en_el_limite(self):
        self.assertEqual(pesos(Decimal("500000.01")), "$500.000,01")

    def test_pesos_cero(self):
        self.assertEqual(pesos(Decimal("0")), "$0,00")

    def test_pesos_un_centavo(self):
        self.assertEqual(pesos(Decimal("0.01")), "$0,01")

    def test_pesos_negativo(self):
        self.assertEqual(pesos(Decimal("-10000.00")), "−$10.000,00")

    def test_pesos_menos_un_centavo(self):
        self.assertEqual(pesos(Decimal("-0.01")), "−$0,01")

    def test_pesos_none(self):
        self.assertEqual(pesos(None), "—")


def _pedido(user=None):
    request = RequestFactory().get("/x/")
    request.user = user or AnonymousUser()
    return request


def _usuario(role, **extra):
    return User.objects.create_user(
        f"u-{role}", email=f"{role}@example.com", password="x", role=role, **extra
    )


class DecoradoresPorRolTest(SistemaTestCase):
    def _codigo(self, vista, user):
        try:
            return vista(_pedido(user)).status_code
        except PermissionDenied:
            return 403

    def test_solo_administrador(self):
        for role, esperado in [("administrator", 200), ("operator", 403), ("professional", 403)]:
            with self.subTest(role=role):
                self.assertEqual(self._codigo(_vista, _usuario(role)), esperado)

    def test_puede_cobrar(self):
        for role, esperado in [("administrator", 200), ("operator", 200), ("professional", 403)]:
            with self.subTest(role=role):
                self.assertEqual(self._codigo(_vista_cobrar, _usuario(role)), esperado)

    def test_solo_profesional(self):
        for role, esperado in [("administrator", 403), ("operator", 403), ("professional", 200)]:
            with self.subTest(role=role):
                self.assertEqual(self._codigo(_vista_profesional, _usuario(role)), esperado)

    def test_anonimo_redirige_al_login(self):
        resp = _vista(_pedido())
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/sistema/login/?next="))

    def test_superuser_con_rol_operador_recibe_403(self):
        u = _usuario("operator", is_superuser=True, is_staff=True)
        with self.assertRaises(PermissionDenied):
            _vista(_pedido(u))
