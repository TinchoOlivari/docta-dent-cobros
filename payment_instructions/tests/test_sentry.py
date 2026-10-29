"""Tests del arranque de Sentry.

Sentry recibe el contexto completo, sin filtrar. Decisión del 12/08/2026.
Estos tests fijan esa decisión: si alguien vuelve a poner scrubbing, fallan
y hay que discutirlo, en vez de perder contexto en silencio.
"""

import sys
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from payment_system.sentry import init_sentry


class InitSentryTests(SimpleTestCase):
    def _init_with_dsn(self, **env):
        """Corre init_sentry con un sentry_sdk falso. Devuelve los kwargs del init."""
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            with patch.dict("os.environ", {"SENTRY_DSN": "https://x@example.test/1", **env}):
                started = init_sentry()
        self.assertTrue(started)
        return fake_sdk.init.call_args.kwargs

    def test_does_nothing_without_a_dsn(self):
        """Sin DSN no arranca. Así local y ci quedan mudos."""
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            with patch.dict("os.environ", {"SENTRY_DSN": ""}):
                self.assertFalse(init_sentry())
        fake_sdk.init.assert_not_called()

    def test_sends_local_variables(self):
        """Sin los locales del frame, un error de pago no se diagnostica."""
        self.assertIs(self._init_with_dsn()["include_local_variables"], True)

    def test_sends_request_bodies(self):
        """El POST de un pago es lo que hace falta para reproducir el error."""
        self.assertEqual(self._init_with_dsn()["max_request_body_size"], "always")

    def test_sends_personal_data(self):
        """Con send_default_pii se sabe qué usuario disparó el error."""
        self.assertIs(self._init_with_dsn()["send_default_pii"], True)

    def test_environment_comes_from_the_environment(self):
        """Producción y staging tienen que quedar separados en Sentry."""
        kwargs = self._init_with_dsn(SENTRY_ENVIRONMENT="production")
        self.assertEqual(kwargs["environment"], "production")
