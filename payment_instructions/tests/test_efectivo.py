from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from payment_instructions.models import Payment, PaymentRecipient, User
from payment_instructions.tests import BaseTestCase


class PaymentEfectivoModelTests(BaseTestCase):
    """Payment.clean(): efectivo no tiene destinatario, transferencia sí lo sigue
    necesitando exactamente como antes."""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1", email="pro@example.com", role=User.PROFESSIONAL
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", role=User.OPERATOR
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test_recipient",
            cbu="1" * 22,
            max_amount=1000,
            priority_order=1,
        )

    def _payment(self, **overrides):
        data = dict(
            amount=100,
            metodo=Payment.EFECTIVO,
            patient_name="Paciente de prueba",
            professional=self.professional,
            operator_user=self.operator,
        )
        data.update(overrides)
        return Payment(**data)

    def test_efectivo_sin_destinatario_es_valido(self):
        payment = self._payment()
        payment.full_clean()  # no debe levantar ValidationError

    def test_efectivo_con_destinatario_asignado_es_invalido(self):
        payment = self._payment(payment_recipient=self.recipient)
        with self.assertRaises(ValidationError) as ctx:
            payment.full_clean()
        self.assertIn("payment_recipient", ctx.exception.message_dict)

    def test_transferencia_sin_destinatario_sigue_siendo_invalida(self):
        payment = self._payment(metodo=Payment.TRANSFERENCIA)
        with self.assertRaises(ValidationError) as ctx:
            payment.full_clean()
        self.assertIn("payment_recipient", ctx.exception.message_dict)

    def test_transferencia_con_destinatario_sigue_siendo_valida(self):
        payment = self._payment(metodo=Payment.TRANSFERENCIA, payment_recipient=self.recipient)
        payment.full_clean()  # no debe levantar ValidationError

    def test_str_no_crashea_sin_destinatario(self):
        payment = self._payment()
        payment.created_at = timezone.now()
        self.assertIn("Efectivo", str(payment))


class CreateCashPaymentViewTests(BaseTestCase):
    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="pro@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.client.login(username="operator1", password="pass")

    def _post(self, **overrides):
        data = {
            "amount": 500,
            "patient_name": "Paciente de prueba",
            "professional_id": self.professional.id,
        }
        data.update(overrides)
        return self.client.post(reverse("payment_instructions:create_cash_payment"), data)

    def test_crea_pago_regular_en_efectivo_sin_comprobante_ni_destinatario(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        payment = Payment.objects.get(pk=response.json()["payment_id"])
        self.assertEqual(payment.metodo, Payment.EFECTIVO)
        self.assertEqual(payment.payment_type, Payment.REGULAR)
        self.assertIsNone(payment.payment_recipient)
        self.assertFalse(payment.proof_of_payment_file)

    def test_crea_seña_en_efectivo(self):
        response = self._post(payment_type="DEPOSIT")

        self.assertEqual(response.status_code, 200)
        payment = Payment.objects.get(pk=response.json()["payment_id"])
        self.assertEqual(payment.payment_type, Payment.DEPOSIT)
        self.assertEqual(payment.metodo, Payment.EFECTIVO)

    def test_no_hay_tope_para_efectivo(self):
        """Sin destinatario, no hay cupo que competir: un monto grande pasa igual."""
        response = self._post(amount=10_000_000)
        self.assertEqual(response.status_code, 200)

    def test_monto_requerido(self):
        response = self._post(amount="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Monto es requerido", response.json()["error"])

    def test_monto_cero_es_invalido(self):
        response = self._post(amount=0)
        self.assertEqual(response.status_code, 400)

    def test_monto_negativo_es_invalido(self):
        response = self._post(amount=-100)
        self.assertEqual(response.status_code, 400)

    def test_monto_uno_es_valido(self):
        """Un peso arriba del mínimo rechazable (cero) tiene que pasar."""
        response = self._post(amount=1)
        self.assertEqual(response.status_code, 200)

    def test_nombre_de_paciente_corto_es_invalido(self):
        response = self._post(patient_name="Jo")
        self.assertEqual(response.status_code, 400)
        self.assertIn("El nombre del paciente debe ser mas largo.", response.json()["error"])

    def test_profesional_no_encontrado(self):
        response = self._post(professional_id=9999)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Profesional no encontrado", response.json()["error"])

    def test_profesional_requiere_permiso_de_operador(self):
        self.client.logout()
        response = self._post()
        # require_payment_creation_permission redirige si no está autenticado
        self.assertEqual(response.status_code, 302)


class PaymentAdminEfectivoDisplayTests(BaseTestCase):
    """Un pago sin payment_recipient no debe crashear las vistas de administrador."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="pro@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.client.login(username="admin", password="pass")
        self.payment = Payment.objects.create(
            amount=500,
            metodo=Payment.EFECTIVO,
            patient_name="Paciente de prueba",
            professional=self.professional,
            operator_user=self.operator,
        )

    def test_changelist_no_crashea_con_pago_en_efectivo(self):
        resp = self.client.get(reverse("admin:payment_instructions_payment_changelist"))
        self.assertEqual(resp.status_code, 200)

    def test_descargar_csv_no_crashea_con_pago_en_efectivo(self):
        resp = self.client.post(
            reverse("admin:payment_instructions_payment_changelist"),
            {
                "action": "download_payments_csv",
                "_selected_action": [self.payment.id],
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        content = b"".join(resp.streaming_content) if resp.streaming else resp.content
        self.assertIn(b"Efectivo", content)
