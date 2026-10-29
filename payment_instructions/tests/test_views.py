from django.test import Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
import json

from payment_instructions.models import PaymentRecipient, Payment
from payment_instructions.tests import BaseTestCase

User = get_user_model()


class AuthViewsTests(BaseTestCase):
    def setUp(self):
        self.client = Client()
        self.password = "pass12345"
        self.user = User.objects.create_user(
            username="op", email="op@example.com", password=self.password
        )

    def test_login_get_when_authenticated_redirects_to_dashboard(self):
        self.client.login(username="op", password=self.password)
        resp = self.client.get(reverse("payment_instructions:operator_login"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("payment_instructions:operator_dashboard"))

    def test_login_get_renders_template(self):
        resp = self.client.get(reverse("payment_instructions:operator_login"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "payment_instructions/login.html")

    def test_login_post_success(self):
        resp = self.client.post(
            reverse("payment_instructions:operator_login"),
            {"username": "op", "password": self.password},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("payment_instructions:operator_dashboard"))

    def test_login_post_failure(self):
        resp = self.client.post(
            reverse("payment_instructions:operator_login"), {"username": "op", "password": "wrong"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "payment_instructions/login.html")

    def test_logout_redirects_to_login(self):
        self.client.login(username="op", password=self.password)
        resp = self.client.get(reverse("payment_instructions:operator_logout"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("payment_instructions:operator_login"))


class DashboardViewTests(BaseTestCase):
    def setUp(self):
        self.client = Client()

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse("payment_instructions:operator_dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("payment_instructions:operator_login"), resp.url)

    def test_dashboard_renders_for_operator(self):
        """Test that an operator can load the dashboard selection page"""
        self.operator_user = User.objects.create_user(
            username="op", email="op@example.com", password="123", role=User.OPERATOR
        )
        self.client.login(username=self.operator_user, password="123")

        resp = self.client.get(reverse("payment_instructions:operator_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "payment_instructions/dashboard.html")

    def test_dashboard_forbidden_for_professional(self):
        """Test that professionals cannot access the operator dashboard"""
        self.client.login(username="spec", password="pass")
        resp = self.client.get(reverse("payment_instructions:operator_dashboard"))
        self.assertEqual(resp.status_code, 302)  # Forbidden


class TransferPaymentFormRenderTests(BaseTestCase):
    """El form de transferencia muestra el destinatario de señas con su propio botón
    de 'Copiar todo', igual que la sección de búsqueda de pago regular."""

    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user(
            username="op", email="op@example.com", password="123", role=User.OPERATOR
        )
        self.client.login(username="op", password="123")

    def test_muestra_boton_copiar_todo_para_destinatario_de_senas(self):
        PaymentRecipient.objects.create(
            name="Solo señas",
            alias="senas",
            cbu="9" * 22,
            recipient_type=PaymentRecipient.DEPOSIT_ONLY,
            max_amount=1,
            priority_order=1,
        )

        resp = self.client.get(reverse("payment_instructions:create_regular_payment_form"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "copyAllDepositBtn")
        self.assertContains(resp, "depositRecipientName")

    def test_sin_destinatario_de_senas_no_rompe_el_boton_copiar(self):
        resp = self.client.get(reverse("payment_instructions:create_regular_payment_form"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "copyAllDepositBtn")


class IniciarTransferenciaViewTests(BaseTestCase):
    """El endpoint que elige destinatario y crea el pendiente. Antes era search_alias
    y solo sugería; ahora toma el cupo en la misma operación, así que además de monto
    exige paciente y profesional."""

    def setUp(self):
        self.client = Client()
        self.operator_user = User.objects.create_user(
            username="op", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional_user = User.objects.create_user(
            username="spec", email="spec@example.com", password="pass", role=User.PROFESSIONAL
        )
        self.client.login(username="op", password="pass")
        self.r1 = PaymentRecipient.objects.create(
            name="R1",
            alias="r1",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=100,
            priority_order=1,
        )
        self.r2 = PaymentRecipient.objects.create(
            name="R2", alias="r2", cbu="2" * 22, max_amount=50, min_threshold=0, priority_order=2
        )

    def post_json(self, data):
        payload = {
            "patient_name": "Paciente Ficticio",
            "professional_id": self.professional_user.id,
        }
        payload.update(data)
        return self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_requires_login(self):
        self.client.logout()
        resp = self.post_json({"amount": 100})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("payment_instructions:operator_login"), resp.url)

    def test_missing_amount(self):
        resp = self.post_json({})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Monto requerido")

    def test_invalid_amount_format(self):
        resp = self.post_json({"amount": "xx"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Formato de monto inválido")

    def test_no_recipient_available(self):
        resp = self.post_json({"amount": 9999999})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No hay destinatarios", resp.json()["error"])

    def test_success_returns_suggested_recipient_and_creates_pendiente(self):
        resp = self.post_json({"amount": 150})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn(data["alias"], ["r1", "r2"])  # either depending on capacity/threshold
        payment = Payment.objects.get(pk=data["payment_id"])
        self.assertEqual(payment.estado, Payment.PENDIENTE)

    def test_professional_cannot_start_transfer(self):
        """Test that professionals cannot access the endpoint"""
        self.client.logout()
        self.client.login(username="spec", password="pass")
        resp = self.post_json({"amount": 150})
        self.assertEqual(resp.status_code, 302)  # Forbidden

    def test_missing_patient_name(self):
        resp = self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps({"amount": 150, "professional_id": self.professional_user.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Nombre del paciente es requerido", resp.json()["error"])

    def test_patient_name_too_short(self):
        resp = self.post_json({"amount": 150, "patient_name": "Jo"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mas largo", resp.json()["error"])

    def test_missing_professional(self):
        resp = self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps({"amount": 150, "patient_name": "Paciente Ficticio"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Profesional es requerido", resp.json()["error"])

    def test_professional_not_found(self):
        resp = self.post_json({"amount": 150, "professional_id": 999999})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Profesional no encontrado", resp.json()["error"])

    def test_amount_below_min_threshold_is_rejected(self):
        """El monto no llega al mínimo de ningún destinatario disponible."""
        self.r2.is_active = False
        self.r2.save()
        resp = self.post_json({"amount": 50})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Payment.objects.count(), 0)


class CompletePaymentViewTests(BaseTestCase):
    """Adjuntar el comprobante a un pendiente. Antes create_payment creaba el pago
    entero; ahora el pago ya existe y esto solo lo cierra."""

    def setUp(self):
        self.client = Client()
        self.operator_user = User.objects.create_user(
            username="op", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional_user = User.objects.create_user(
            username="spec_user",
            email="spec_user@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.client.login(username="op", password="pass")
        self.recipient = PaymentRecipient.objects.create(
            name="R", alias="r", cbu="1" * 22, max_amount=1000, min_threshold=100, priority_order=1
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
            is_active=True,
        )
        self.payment = Payment.objects.crear_pendiente(
            payment_recipient=self.recipient,
            amount=200,
            operator_user=self.operator_user,
            patient_name="John Doe",
            professional=self.professional,
        )

    def url(self):
        return reverse("payment_instructions:complete_payment")

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(self.url(), {})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("payment_instructions:operator_login"), resp.url)

    def test_missing_payment_id(self):
        resp = self.client.post(self.url(), {})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("payment_id es requerido", resp.json()["error"])

    def test_payment_not_found(self):
        resp = self.client.post(self.url(), {"payment_id": 999999})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Pago no encontrado", resp.json()["error"])

    def test_missing_proof_file(self):
        resp = self.client.post(self.url(), {"payment_id": self.payment.id})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("El comprobante es requerido.", resp.json()["error"])

    def test_file_too_large(self):
        big_content = b"0" * (5 * 1024 * 1024 + 1)
        big_file = SimpleUploadedFile("big.pdf", big_content, content_type="application/pdf")
        resp = self.client.post(
            self.url(), {"payment_id": self.payment.id, "proof_of_payment_file": big_file}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("demasiado grande", resp.json()["error"])

    def test_invalid_file_type(self):
        bad_file = SimpleUploadedFile("bad.txt", b"hello", content_type="text/plain")
        resp = self.client.post(
            self.url(), {"payment_id": self.payment.id, "proof_of_payment_file": bad_file}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Tipo de archivo no válido", resp.json()["error"])

    def test_compression_error_returns_400(self):
        # Un jpeg que no es una imagen: el compresor devuelve el original o falla, y
        # en los dos casos la respuesta tiene que ser JSON, no un 500.
        bad_image = SimpleUploadedFile("img.jpg", b"not-an-image", content_type="image/jpeg")
        resp = self.client.post(
            self.url(), {"payment_id": self.payment.id, "proof_of_payment_file": bad_image}
        )
        self.assertIn(resp.status_code, [200, 400])

    def test_success_completes_payment(self):
        pdf = SimpleUploadedFile("rec.pdf", b"%PDF-1.4 data", content_type="application/pdf")
        resp = self.client.post(
            self.url(), {"payment_id": self.payment.id, "proof_of_payment_file": pdf}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.estado, Payment.COMPLETADO)
        self.assertTrue(self.payment.proof_of_payment_file)

    def test_professional_cannot_complete_payment(self):
        """Test that professionals cannot complete payments"""
        self.client.logout()
        self.client.login(username="spec_user", password="pass")
        pdf = SimpleUploadedFile("rec.pdf", b"%PDF-1.4 data", content_type="application/pdf")
        resp = self.client.post(
            self.url(), {"payment_id": self.payment.id, "proof_of_payment_file": pdf}
        )
        self.assertEqual(resp.status_code, 302)  # Forbidden


class CreateDepositViewTests(BaseTestCase):
    """La seña por transferencia. No pasa por búsqueda de destinatario (va al único
    DEPOSIT_ONLY activo y no consume cupo), pero el comprobante es obligatorio igual
    que en una transferencia regular: nace COMPLETADO o no nace."""

    def setUp(self):
        self.client = Client()
        self.operator_user = User.objects.create_user(
            username="op", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.deposit_recipient = PaymentRecipient.objects.create(
            name="Cuenta de señas",
            alias="cuenta.de.senias",
            cbu="9" * 22,
            recipient_type=PaymentRecipient.DEPOSIT_ONLY,
            max_amount=1,
            priority_order=1,
        )
        self.client.login(username="op", password="pass")

    def url(self):
        return reverse("payment_instructions:create_deposit")

    def _payload(self, **overrides):
        data = {
            "amount": 5000,
            "patient_name": "Paciente Ficticio",
            "professional_id": self.professional.id,
        }
        data.update(overrides)
        return data

    def test_sin_comprobante_devuelve_400_y_no_crea_nada(self):
        resp = self.client.post(self.url(), self._payload())
        self.assertEqual(resp.status_code, 400)
        self.assertIn("El comprobante es requerido.", resp.json()["error"])
        self.assertEqual(Payment.objects.count(), 0)

    def test_con_comprobante_nace_completada(self):
        pdf = SimpleUploadedFile("rec.pdf", b"%PDF-1.4 data", content_type="application/pdf")
        resp = self.client.post(self.url(), self._payload(proof_of_payment_file=pdf))
        self.assertEqual(resp.status_code, 200)
        payment = Payment.objects.get(pk=resp.json()["payment_id"])
        self.assertEqual(payment.estado, Payment.COMPLETADO)
        self.assertEqual(payment.payment_type, Payment.DEPOSIT)
        self.assertTrue(payment.proof_of_payment_file)

    def test_una_seña_no_consume_cupo(self):
        pdf = SimpleUploadedFile("rec.pdf", b"%PDF-1.4 data", content_type="application/pdf")
        self.client.post(self.url(), self._payload(amount=999_999, proof_of_payment_file=pdf))
        self.assertTrue(self.deposit_recipient.can_receive_amount(999_999))

    def test_tipo_de_archivo_invalido_devuelve_400(self):
        bad = SimpleUploadedFile("bad.txt", b"hello", content_type="text/plain")
        resp = self.client.post(self.url(), self._payload(proof_of_payment_file=bad))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Tipo de archivo no válido", resp.json()["error"])
        self.assertEqual(Payment.objects.count(), 0)
