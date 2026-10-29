"""Los fallos del camino del dinero tienen que llegar a Sentry.

Sentry crea un evento desde el nivel ERROR. Un `logger.warning` queda como
breadcrumb y nadie se entera. Estos tests fijan el nivel, no el texto.
"""

import json
from unittest.mock import patch

from django.urls import reverse

from . import BaseTestCase
from ..models import User


class MoneyPathErrorLoggingTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.operator = User.objects.create_user(
            username="op_logs",
            email="op_logs@example.com",
            password="123",
            role=User.OPERATOR,
        )
        # El endpoint valida paciente y profesional antes de llegar a la parte que
        # este test hace fallar: sin un profesional válido cortaría en un 400.
        self.professional = User.objects.create_user(
            username="prof_logs",
            email="prof_logs@example.com",
            password="123",
            role=User.PROFESSIONAL,
        )
        self.client.login(username="op_logs", password="123")

    def test_iniciar_transferencia_logs_at_error_when_it_returns_500(self):
        """Antes devolvía 500 sin loguear nada. Sentry no veía el fallo."""
        with patch(
            "payment_instructions.views.validate_payment_amount",
            side_effect=RuntimeError("la base se cayó"),
        ):
            with self.assertLogs("payment_instructions", level="ERROR") as logs:
                resp = self.client.post(
                    reverse("payment_instructions:iniciar_transferencia"),
                    data=json.dumps(
                        {
                            "amount": "1000",
                            "patient_name": "Paciente Ficticio",
                            "professional_id": self.professional.id,
                        }
                    ),
                    content_type="application/json",
                )

        self.assertEqual(resp.status_code, 500)
        self.assertTrue(any(r.levelname == "ERROR" for r in logs.records))
        self.assertTrue(any(r.exc_info for r in logs.records), "falta el traceback")
