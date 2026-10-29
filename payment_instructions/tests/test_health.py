"""Tests for the /health/ endpoint used by the staging deploy."""

from unittest.mock import patch

from . import BaseTestCase


class HealthEndpointTests(BaseTestCase):
    def test_health_returns_200_without_authentication(self):
        """El deploy pega sin credenciales. Tiene que responder 200."""
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_health_returns_503_when_the_database_is_down(self):
        """Sin base, el sitio está en pie pero no sirve. El deploy debe fallar."""
        with patch("payment_system.urls.connection.ensure_connection", side_effect=OSError):
            resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["database"], "down")

    def test_health_is_not_swallowed_by_the_catch_all_redirect(self):
        """urls.py tiene un catch-all `^.*/$`. Si /health/ cae ahí, devuelve 302."""
        resp = self.client.get("/health/")
        self.assertNotEqual(resp.status_code, 302)
