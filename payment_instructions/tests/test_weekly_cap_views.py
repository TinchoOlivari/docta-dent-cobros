from django.test import Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from datetime import timedelta
import json

from payment_instructions.models import PaymentRecipient, Payment
from payment_instructions.tests import BaseTestCase

User = get_user_model()


class WeeklyCapSearchAliasViewTests(BaseTestCase):
    """Test search_alias view with weekly cap constraints"""

    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )

        self.client.login(username="operator1", password="pass")

        # Create recipients with different weekly caps
        self.recipient_low_cap = PaymentRecipient.objects.create(
            name="Low Weekly Cap",
            alias="low_cap",
            cbu="1" * 22,
            max_amount=2000,
            weekly_cap_amount=300,  # Low weekly cap
            min_threshold=50,
            priority_order=1,
        )

        self.recipient_high_cap = PaymentRecipient.objects.create(
            name="High Weekly Cap",
            alias="high_cap",
            cbu="2" * 22,
            max_amount=2000,
            weekly_cap_amount=800,  # High weekly cap
            min_threshold=50,
            priority_order=2,
        )

        self.recipient_no_cap = PaymentRecipient.objects.create(
            name="No Weekly Cap",
            alias="no_cap",
            cbu="3" * 22,
            max_amount=2000,
            weekly_cap_amount=0,  # No weekly cap
            min_threshold=50,
            priority_order=3,
        )

    def _create_payment(self, recipient, amount):
        """Helper to create payment"""
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        return Payment.objects.create(
            amount=amount,
            payment_recipient=recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

    def _post_search_alias(self, amount):
        """Elige destinatario y crea el pendiente, en un solo pedido.

        Antes esto solo sugería (search_alias). Ahora el mismo endpoint toma el cupo,
        así que además del monto manda paciente y profesional."""
        return self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(
                {
                    "amount": amount,
                    "patient_name": "Paciente Ficticio",
                    "professional_id": self.professional.id,
                }
            ),
            content_type="application/json",
        )

    def test_search_alias_respects_weekly_cap_constraints(self):
        """Test that search_alias considers weekly cap when suggesting recipients"""
        # Create payment that uses up low cap recipient's weekly allowance
        self._create_payment(self.recipient_low_cap, 250)  # Remaining weekly: 50

        # Search for amount that exceeds low cap remaining but fits others
        response = self._post_search_alias(100)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Should not suggest low_cap recipient
        self.assertNotEqual(data["alias"], "low_cap")
        # Should suggest one of the available recipients
        self.assertIn(data["alias"], ["high_cap", "no_cap"])

    def test_search_alias_suggests_recipient_with_sufficient_weekly_cap(self):
        """Test that search_alias suggests recipient with sufficient weekly capacity"""
        # Create payments that partially fill weekly caps
        self._create_payment(self.recipient_low_cap, 100)  # Remaining: 200
        self._create_payment(self.recipient_high_cap, 200)  # Remaining: 600

        # Search for amount that fits in high cap but not low cap
        response = self._post_search_alias(250)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Should suggest high_cap or no_cap (both can handle 250)
        self.assertIn(data["alias"], ["high_cap", "no_cap"])
        self.assertNotEqual(data["alias"], "low_cap")

    def test_search_alias_no_recipients_available_due_to_weekly_caps(self):
        """Test search_alias when no recipients available due to weekly caps"""
        # Fill up all recipients' weekly caps
        self._create_payment(self.recipient_low_cap, 300)  # Weekly cap reached
        self._create_payment(self.recipient_high_cap, 800)  # Weekly cap reached

        # Fill up no_cap recipient's monthly capacity
        for _ in range(20):
            self._create_payment(self.recipient_no_cap, 100)  # Total: 2000, monthly cap reached

        # Search for any amount
        response = self._post_search_alias(50)

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("No hay destinatarios", data["error"])

    def test_search_alias_prioritizes_by_order_with_weekly_caps(self):
        """Test that search_alias respects priority order among available recipients"""
        # Create payment that makes highest priority recipient unavailable
        self._create_payment(self.recipient_low_cap, 280)  # Remaining: 20, can't handle 100

        # Search for amount that fits in remaining recipients
        response = self._post_search_alias(100)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Should suggest high_cap (priority 2) over no_cap (priority 3)
        self.assertEqual(data["alias"], "high_cap")

    def test_search_alias_with_weekly_cap_exactly_at_limit(self):
        """Test search_alias when recipient is exactly at weekly limit"""
        # Create payment that exactly reaches weekly cap
        self._create_payment(self.recipient_low_cap, 300)  # Exactly at weekly cap

        # Search for any amount
        response = self._post_search_alias(50)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Should not suggest low_cap recipient
        self.assertNotEqual(data["alias"], "low_cap")

    def test_search_alias_ignores_weekly_cap_when_disabled(self):
        """Test that search_alias ignores weekly cap when it's disabled (0)"""
        # Fill up recipients with weekly caps
        self._create_payment(self.recipient_low_cap, 300)  # Weekly cap reached
        self._create_payment(self.recipient_high_cap, 800)  # Weekly cap reached

        # no_cap recipient should still be available (no weekly limit)
        response = self._post_search_alias(500)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["alias"], "no_cap")


class WeeklyCapCreatePaymentViewTests(BaseTestCase):
    """Test create_payment view with weekly cap constraints"""

    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )

        self.client.login(username="operator1", password="pass")

        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test_recipient",
            cbu="1" * 22,
            max_amount=2000,
            weekly_cap_amount=500,
            min_threshold=50,
            priority_order=1,
        )

    def _create_payment_via_view(self, amount, recipient_id=None, patient_name="John Doe"):
        """Crea el pago de punta a punta: iniciar (toma el cupo) y completar.

        El chequeo de tope vive ahora en el primer paso, así que si el destinatario no
        puede recibir el monto se devuelve esa respuesta y no se llega a completar.
        recipient_id se ignora: el destinatario lo elige el sistema por prioridad."""
        inicio = self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(
                {
                    "amount": amount,
                    "patient_name": patient_name,
                    "professional_id": self.professional.id,
                }
            ),
            content_type="application/json",
        )
        if inicio.status_code != 200:
            return inicio

        file = SimpleUploadedFile("test.pdf", b"%PDF-1.4 data", content_type="application/pdf")
        return self.client.post(
            reverse("payment_instructions:complete_payment"),
            {
                "payment_id": inicio.json()["payment_id"],
                "proof_of_payment_file": file,
            },
        )

    def _create_existing_payment(self, amount):
        """Helper to create existing payment directly"""
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        return Payment.objects.create(
            amount=amount,
            payment_recipient=self.recipient,
            patient_name="Existing Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

    def test_create_payment_within_weekly_cap(self):
        """Test creating payment within weekly cap succeeds"""
        response = self._create_payment_via_view(300)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Verify payment was created
        payment = Payment.objects.get(id=data["payment_id"])
        self.assertEqual(payment.amount, 300)

    def test_create_payment_exceeds_weekly_cap(self):
        """Test creating payment that exceeds weekly cap fails"""
        # Create existing payment
        self._create_existing_payment(300)

        # Try to create payment that exceeds weekly cap
        response = self._create_payment_via_view(250)  # 300 + 250 = 550 > 500

        self.assertEqual(response.status_code, 400)
        data = response.json()

        self.assertIn("No hay destinatarios disponibles", data["error"])

    def test_create_payment_exactly_reaches_weekly_cap(self):
        """Test creating payment that exactly reaches weekly cap succeeds"""
        # Create existing payment
        self._create_existing_payment(300)

        # Create payment that exactly reaches weekly cap
        response = self._create_payment_via_view(200)  # 300 + 200 = 500 (exactly cap)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

    def test_create_payment_weekly_cap_already_reached(self):
        """Test creating payment when weekly cap is already reached"""
        # Create payments that reach weekly cap
        self._create_existing_payment(300)
        self._create_existing_payment(200)  # Total: 500 (cap reached)

        # Try to create any additional payment
        response = self._create_payment_via_view(50)

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("No hay destinatarios disponibles", data["error"])

    def test_create_payment_no_weekly_cap_only_monthly_limit(self):
        """Test creating payment for recipient with no weekly cap"""
        recipient_no_cap = PaymentRecipient.objects.create(
            name="No Cap",
            alias="no_cap",
            cbu="2" * 22,
            max_amount=1000,
            weekly_cap_amount=0,  # No weekly cap
            min_threshold=50,
            priority_order=2,
        )

        # Sin el de prioridad 1 en juego, el pago cae en el que no tiene tope semanal.
        self.recipient.is_active = False
        self.recipient.save()

        # Should be limited only by monthly cap
        response = self._create_payment_via_view(800, recipient_id=recipient_no_cap.id)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

    def test_create_payment_weekly_vs_monthly_cap_precedence(self):
        """Test that create_payment respects both weekly and monthly caps"""
        # Create recipient where weekly cap is more restrictive
        recipient = PaymentRecipient.objects.create(
            name="Restrictive Weekly",
            alias="restrictive",
            cbu="3" * 22,
            max_amount=2000,  # Monthly cap
            weekly_cap_amount=300,  # Weekly cap (more restrictive)
            min_threshold=50,
            priority_order=3,
        )

        # El sistema elige destinatario por prioridad: se deja solo al restrictivo para
        # que la respuesta hable de él y no del de prioridad 1.
        self.recipient.is_active = False
        self.recipient.save()

        # Try amount within monthly but exceeds weekly
        response = self._create_payment_via_view(400, recipient_id=recipient.id)

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("No hay destinatarios disponibles", data["error"])

        # Try amount within weekly cap
        response = self._create_payment_via_view(250, recipient_id=recipient.id)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])


class WeeklyCapViewIntegrationTests(BaseTestCase):
    """Integration tests for weekly cap functionality across views"""

    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )

        self.client.login(username="operator1", password="pass")

        self.recipient = PaymentRecipient.objects.create(
            name="Integration Test",
            alias="integration",
            cbu="1" * 22,
            max_amount=2000,
            weekly_cap_amount=500,
            min_threshold=50,
            priority_order=1,
        )

    def _post_search_alias(self, amount):
        """Elige destinatario y crea el pendiente, en un solo pedido.

        Antes esto solo sugería (search_alias). Ahora el mismo endpoint toma el cupo,
        así que además del monto manda paciente y profesional."""
        return self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(
                {
                    "amount": amount,
                    "patient_name": "Paciente Ficticio",
                    "professional_id": self.professional.id,
                }
            ),
            content_type="application/json",
        )

    def _create_payment_via_view(self, amount, payment_id=None):
        """Completa un pago pendiente. Si no se pasa payment_id, lo crea primero.

        El chequeo de tope quedó en el paso de iniciar: cuando el destinatario no puede
        recibir el monto, se devuelve esa respuesta sin llegar a completar."""
        if payment_id is None:
            inicio = self._post_search_alias(amount)
            if inicio.status_code != 200:
                return inicio
            payment_id = inicio.json()["payment_id"]

        file = SimpleUploadedFile("test.pdf", b"%PDF-1.4 data", content_type="application/pdf")
        return self.client.post(
            reverse("payment_instructions:complete_payment"),
            {"payment_id": payment_id, "proof_of_payment_file": file},
        )

    def _crear_pago_completo(self, amount, patient_name="John Doe"):
        """Crea el pago de punta a punta: iniciar (elige destinatario y toma el cupo)
        y completar con el comprobante.

        El destinatario ya no se manda: lo elige el sistema por prioridad. Si ninguno
        puede recibir el monto, se devuelve la respuesta del primer paso."""
        inicio = self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(
                {
                    "amount": amount,
                    "patient_name": patient_name,
                    "professional_id": self.professional.id,
                }
            ),
            content_type="application/json",
        )
        if inicio.status_code != 200:
            return inicio

        file = SimpleUploadedFile("test.pdf", b"%PDF-1.4 data", content_type="application/pdf")
        return self.client.post(
            reverse("payment_instructions:complete_payment"),
            {"payment_id": inicio.json()["payment_id"], "proof_of_payment_file": file},
        )

    def test_full_workflow_with_weekly_cap_constraints(self):
        """Test complete workflow from search to payment creation with weekly caps"""
        # Step 1: Search for recipient for amount within weekly cap
        search_response = self._post_search_alias(300)

        self.assertEqual(search_response.status_code, 200)
        search_data = search_response.json()
        self.assertTrue(search_data["success"])
        self.assertEqual(search_data["alias"], "integration")

        # Step 2: Create payment, confirmando la reserva que tomó la sugerencia del paso 1
        payment_response = self._create_payment_via_view(300, payment_id=search_data["payment_id"])

        self.assertEqual(payment_response.status_code, 200)
        payment_data = payment_response.json()
        self.assertTrue(payment_data["success"])

        # Step 3: Search for another amount that would exceed weekly cap
        search_response2 = self._post_search_alias(250)  # 300 + 250 = 550 > 500

        # Should either return error or suggest different recipient
        # (depends on whether other recipients exist)
        self.assertEqual(search_response2.status_code, 400)
        search_data2 = search_response2.json()

        self.assertIn("No hay destinatarios disponibles para este monto", search_data2["error"])

        # Step 4: Try to create payment that exceeds weekly cap
        payment_response2 = self._create_payment_via_view(250)

        self.assertEqual(payment_response2.status_code, 400)
        payment_data2 = payment_response2.json()
        self.assertIn("No hay destinatarios disponibles para este monto", search_data2["error"])

        # Step 5: Create payment within remaining weekly capacity
        payment_response3 = self._create_payment_via_view(200)  # 300 + 200 = 500 (exactly cap)

        self.assertEqual(payment_response3.status_code, 200)
        payment_data3 = payment_response3.json()
        self.assertTrue(payment_data3["success"])

        # Step 6: Verify no more payments can be made this week
        search_response4 = self._post_search_alias(50)

        self.assertEqual(search_response4.status_code, 400)
        search_data4 = search_response4.json()
        self.assertIn("No hay destinatarios disponibles para este monto", search_data2["error"])

    def test_workflow_with_multiple_recipients_and_weekly_caps(self):
        """Test workflow with multiple recipients having different weekly caps"""
        # Create additional recipient with higher weekly cap
        recipient_high = PaymentRecipient.objects.create(
            name="High Cap",
            alias="high_cap",
            cbu="2" * 22,
            max_amount=2000,
            weekly_cap_amount=800,
            min_threshold=50,
            priority_order=2,
        )

        # Fill up first recipient's weekly cap
        payment_response1 = self._create_payment_via_view(500)  # Exactly at cap

        self.assertEqual(payment_response1.status_code, 200)

        # Search should now suggest the second recipient
        search_response = self._post_search_alias(300)

        self.assertEqual(search_response.status_code, 200)
        search_data = search_response.json()
        self.assertTrue(search_data["success"])
        self.assertEqual(search_data["alias"], "high_cap")

        # Create payment for second recipient
        payment_response2 = self._crear_pago_completo(
            amount=300,
            patient_name="Jane Doe",
        )

        self.assertEqual(payment_response2.status_code, 200)
        payment_data2 = payment_response2.json()
        self.assertTrue(payment_data2["success"])

    def test_workflow_across_week_boundaries(self):
        """Test that workflow properly handles week boundaries"""
        # Create payment in current week
        payment_response1 = self._create_payment_via_view(400)

        self.assertEqual(payment_response1.status_code, 200)

        # Simulate time passing to next week
        now = timezone.now()
        next_week = now + timedelta(days=8)

        # In a real scenario, we'd need to mock timezone.now()
        # For this test, we'll verify that the recipient's weekly calculations
        # would reset in the next week

        # Verify current week usage
        self.assertEqual(self.recipient.get_current_week_received(), 400)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 100)

        # Create payment for remaining weekly capacity
        payment_response2 = self._create_payment_via_view(100)

        self.assertEqual(payment_response2.status_code, 200)

        # Verify weekly cap is now reached
        self.assertEqual(self.recipient.get_current_week_received(), 500)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 0)


class WeeklyCapViewErrorHandlingTests(BaseTestCase):
    """Test error handling in views with weekly cap constraints"""

    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )

        self.client.login(username="operator1", password="pass")

    def _crear_pago_completo(self, amount, patient_name="John Doe"):
        """Crea el pago de punta a punta: iniciar (elige destinatario y toma el cupo)
        y completar con el comprobante.

        El destinatario ya no se manda: lo elige el sistema por prioridad. Si ninguno
        puede recibir el monto, se devuelve la respuesta del primer paso."""
        inicio = self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(
                {
                    "amount": amount,
                    "patient_name": patient_name,
                    "professional_id": self.professional.id,
                }
            ),
            content_type="application/json",
        )
        if inicio.status_code != 200:
            return inicio

        file = SimpleUploadedFile("test.pdf", b"%PDF-1.4 data", content_type="application/pdf")
        return self.client.post(
            reverse("payment_instructions:complete_payment"),
            {"payment_id": inicio.json()["payment_id"], "proof_of_payment_file": file},
        )

    def test_search_alias_error_messages_mention_weekly_limits(self):
        """Test that error messages appropriately mention weekly limits"""
        # Create recipient with small weekly cap
        recipient = PaymentRecipient.objects.create(
            name="Small Cap",
            alias="small_cap",
            cbu="1" * 22,
            max_amount=2000,
            weekly_cap_amount=100,
            min_threshold=50,
            priority_order=1,
        )

        # Fill weekly cap
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        Payment.objects.create(
            amount=100,
            payment_recipient=recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

        # Search for any amount
        response = self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(
                {
                    "amount": 50,
                    "patient_name": "Paciente Ficticio",
                    "professional_id": self.professional.id,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        # Error message should indicate no recipients available
        self.assertIn("No hay destinatarios", data["error"])

    def test_create_payment_error_messages_for_weekly_cap_exceeded(self):
        """Test that create_payment provides clear error messages for weekly cap violations"""
        recipient = PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test_recipient",
            cbu="1" * 22,
            max_amount=2000,
            weekly_cap_amount=300,
            min_threshold=50,
            priority_order=1,
        )

        # Create existing payment
        content = b"fake pdf content"
        file = SimpleUploadedFile("existing.pdf", content, content_type="application/pdf")
        Payment.objects.create(
            amount=200,
            payment_recipient=recipient,
            patient_name="Existing Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

        # Try to create payment that exceeds weekly cap
        response = self._crear_pago_completo(
            amount=150,  # 200 + 150 = 350 > 300
            patient_name="New Patient",
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("No hay destinatarios disponibles", data["error"])

    def test_view_handles_edge_cases_gracefully(self):
        """Test that views handle edge cases gracefully"""
        # Test with recipient that has weekly cap equal to min threshold
        recipient = PaymentRecipient.objects.create(
            name="Edge Case",
            alias="edge_case",
            cbu="1" * 22,
            max_amount=1000,
            weekly_cap_amount=100,
            min_threshold=100,  # Same as weekly cap
            priority_order=1,
        )

        # Should be able to create exactly one payment per week
        response = self._crear_pago_completo(amount=100, patient_name="Edge Patient")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Second payment should fail
        response2 = self._crear_pago_completo(amount=100, patient_name="Edge Patient 2")

        self.assertEqual(response2.status_code, 400)
        data2 = response2.json()
        self.assertIn("No hay destinatarios disponibles", data2["error"])
