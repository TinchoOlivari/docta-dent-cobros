"""Tests del aviso visual de staging (admin + pantallas del operador)."""

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from . import BaseTestCase
from ..context_processors import environment

User = get_user_model()


class EnvironmentContextProcessorTests(BaseTestCase):
    @override_settings(ENVIRONMENT="staging")
    def test_is_staging_true_when_environment_is_staging(self):
        self.assertTrue(environment(None)["is_staging"])

    def test_is_staging_false_by_default(self):
        """Default de base.py es 'production': no debe mostrar el aviso."""
        self.assertFalse(environment(None)["is_staging"])

    @override_settings(ENVIRONMENT="production")
    def test_is_staging_false_in_production(self):
        self.assertFalse(environment(None)["is_staging"])


class StagingBadgeRenderTests(BaseTestCase):
    @override_settings(ENVIRONMENT="staging")
    def test_login_page_shows_staging_badge(self):
        resp = self.client.get("/login/")
        self.assertContains(resp, "Ambiente de staging")

    def test_login_page_hides_staging_badge_by_default(self):
        resp = self.client.get("/login/")
        self.assertNotContains(resp, "Ambiente de staging")

    @override_settings(ENVIRONMENT="staging")
    def test_dashboard_shows_staging_badge(self):
        User.objects.create_user(
            username="op", email="op@example.com", password="123", role=User.OPERATOR
        )
        self.client.login(username="op", password="123")
        resp = self.client.get(reverse("payment_instructions:operator_dashboard"))
        self.assertContains(resp, "Ambiente de staging")

    @override_settings(ENVIRONMENT="staging")
    def test_regular_payment_form_shows_staging_badge(self):
        User.objects.create_user(
            username="op", email="op@example.com", password="123", role=User.OPERATOR
        )
        self.client.login(username="op", password="123")
        resp = self.client.get(reverse("payment_instructions:create_regular_payment_form"))
        self.assertContains(resp, "Ambiente de staging")

    @override_settings(ENVIRONMENT="staging")
    def test_payment_method_selection_shows_staging_badge(self):
        User.objects.create_user(
            username="op", email="op@example.com", password="123", role=User.OPERATOR
        )
        self.client.login(username="op", password="123")
        resp = self.client.get(reverse("payment_instructions:payment_method_selection"))
        self.assertContains(resp, "Ambiente de staging")


class AdminStagingBadgeTests(BaseTestCase):
    @override_settings(ENVIRONMENT="staging")
    def test_admin_login_page_shows_staging_badge(self):
        resp = self.client.get(reverse("admin:login"))
        self.assertContains(resp, "Ambiente de staging")

    def test_admin_login_page_hides_staging_badge_by_default(self):
        resp = self.client.get(reverse("admin:login"))
        self.assertNotContains(resp, "Ambiente de staging")
