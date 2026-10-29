"""Base de las pruebas con navegador (016, research P10). Se corren con `make e2e`, no con `make test`. Solo 1280 px.

Playwright (sync) + Chromium contra un servidor real. Los archivos se llaman `e2e_*.py` para que
`manage.py test` no los tome por el patrón por defecto.
"""

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import Client
from playwright.sync_api import sync_playwright

from payment_instructions.models import User
from sistema.core.tests import SistemaTransactionTestCase

CLAVE = "clave-de-siempre-1"


def elegir(pagina, selector, valor=None, label=None):
    """Elige una opción de un select con Tom Select (023/FR-004): clic en `.ts-control`, escribe en
    `.dropdown-input` y clic en la opción. Por `valor` (data-value) o por el texto `label`."""
    envoltorio = f"{selector} + .ts-wrapper"
    pagina.locator(f"{envoltorio} .ts-control").click()
    if label:
        pagina.locator(f"{envoltorio} .dropdown-input").fill(label)
        opcion = pagina.locator(f"{envoltorio} .option", has_text=label).first
    else:
        opcion = pagina.locator(f'{envoltorio} .option[data-value="{valor}"]')
    opcion.click()


class E2ECase(SistemaTransactionTestCase, StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._pw = sync_playwright().start()
        cls._navegador = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls._navegador.close()
        cls._pw.stop()
        # El servidor real puede crear ContentType al vuelo después del último flush: se vacía de
        # nuevo (sin post_migrate) para que la restauración de los datos sembrados no choque.
        for alias in cls._databases_names(include_mirrors=False):
            call_command(
                "flush", verbosity=0, interactive=False, database=alias, inhibit_post_migrate=True
            )
        super().tearDownClass()

    def nueva_pagina(self, rol=None, ancho=1280, alto=900):
        """Página nueva en un contexto propio. Con `rol`, ya con sesión iniciada."""
        contexto = self._navegador.new_context(
            viewport={"width": ancho, "height": alto}, locale="es-AR"
        )
        self.addCleanup(contexto.close)
        if rol:
            usuario, _ = User.objects.get_or_create(username=f"e2e-{rol}", defaults={"role": rol})
            usuario.role = rol
            usuario.set_password(CLAVE)
            usuario.save()
            cliente = Client()
            cliente.force_login(usuario)
            host = self.live_server_url.split("//")[1].split(":")[0]
            contexto.add_cookies(
                [
                    {
                        "name": "sessionid",
                        "value": cliente.cookies["sessionid"].value,
                        "domain": host,
                        "path": "/",
                    }
                ]
            )
        pagina = contexto.new_page()
        pagina.set_default_timeout(10000)
        return pagina

    def url(self, ruta):
        return f"{self.live_server_url}{ruta}"
