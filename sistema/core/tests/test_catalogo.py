from django.test import override_settings
from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.usuarios import roles

CLAVE = "clave-de-siempre-1"


class CatalogoTest(SistemaTestCase):
    def _entrar(self, rol):
        u = User.objects.create(username=f"u-{rol}", role=rol)
        u.set_password(CLAVE)
        u.save()
        self.client.login(username=u.username, password=CLAVE)

    @override_settings(DEBUG=False)
    def test_con_debug_apagado_nadie_lo_ve(self):
        casos = ((roles.ADMIN, 404), (roles.OPERATOR, 404), (roles.PROFESSIONAL, 404))  # 021/FR-053
        for rol, esperado in casos:
            with self.subTest(rol=rol):
                self.client.logout()
                self._entrar(rol)
                self.assertEqual(self.client.get(reverse("core:catalogo")).status_code, esperado)

    @override_settings(DEBUG=False)
    def test_sin_sesion_y_sin_debug_es_404(self):
        self.assertEqual(self.client.get(reverse("core:catalogo")).status_code, 404)

    @override_settings(DEBUG=True)
    def test_sin_sesion_va_al_login(self):
        r = self.client.get(reverse("core:catalogo"))
        self.assertRedirects(
            r,
            f"{reverse('usuarios:login')}?next={reverse('core:catalogo')}",
            fetch_redirect_response=False,
        )

    @override_settings(DEBUG=True)
    def test_con_debug_encendido_el_operador_no_entra(self):
        self._entrar(roles.OPERATOR)
        self.assertEqual(self.client.get(reverse("core:catalogo")).status_code, 403)
