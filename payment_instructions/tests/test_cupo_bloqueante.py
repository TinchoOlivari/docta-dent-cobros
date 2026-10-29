"""El cupo queda tomado desde que se elige el destinatario, no desde que llega el
comprobante.

Es la razón de ser del estado PENDIENTE. Si alguno de estos tests se rompe, el sistema
volvió a poder ofrecer el mismo cupo a dos operadores a la vez, y el segundo se entera
recién cuando el paciente ya transfirió.

El test de concurrencia real necesita Postgres: SQLite ignora select_for_update en
silencio. El CI corre sobre Postgres; en local, con la config de docker-compose.local.
"""

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from payment_instructions.models import Payment, PaymentRecipient, User
from payment_instructions.tests import BaseTestCase


class CupoBloqueanteTests(BaseTestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="operador", email="operador@example.com", password="x", role=User.OPERATOR
        )
        self.otro_operator = User.objects.create_user(
            username="otro", email="otro@example.com", password="x", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="profesional",
            email="profesional@example.com",
            password="x",
            role=User.PROFESSIONAL,
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Cuenta principal",
            alias="cuenta.principal",
            cbu="1234567890123456789012",
            max_amount=200_000,
            priority_order=1,
        )

    def _crear_pendiente(self, amount, operator=None):
        return Payment.objects.crear_pendiente(
            payment_recipient=self.recipient,
            amount=amount,
            operator_user=operator or self.operator,
            patient_name="Paciente Ficticio",
            professional=self.professional,
        )

    # ---- un pendiente ocupa cupo ----

    def test_un_pendiente_consume_cupo_mensual(self):
        self._crear_pendiente(50_000)
        self.assertEqual(self.recipient.get_current_month_received(), 50_000)
        self.assertEqual(self.recipient.get_remaining_amount(), 150_000)

    def test_un_completado_consume_cupo_mensual(self):
        payment = self._crear_pendiente(50_000)
        payment.completar(
            SimpleUploadedFile("comprobante.png", b"contenido", content_type="image/png")
        )
        self.assertEqual(self.recipient.get_current_month_received(), 50_000)

    def test_un_cancelado_libera_el_cupo(self):
        payment = self._crear_pendiente(50_000)
        self.assertEqual(self.recipient.get_current_month_received(), 50_000)
        payment.cancelar(motivo=Payment.PACIENTE_NO_PAGO, usuario=self.operator)
        self.assertEqual(self.recipient.get_current_month_received(), 0)
        self.assertEqual(self.recipient.get_remaining_amount(), 200_000)

    def test_completar_no_cambia_el_cupo_consumido(self):
        """El cupo se tomó al crear el pendiente. Completar no vuelve a descontarlo."""
        payment = self._crear_pendiente(80_000)
        antes = self.recipient.get_current_month_received()
        payment.completar(
            SimpleUploadedFile("comprobante.png", b"contenido", content_type="image/png")
        )
        self.assertEqual(self.recipient.get_current_month_received(), antes)

    # ---- casos límite del tope mensual ----

    def test_pendiente_justo_en_el_tope_entra(self):
        self.assertIsNotNone(self._crear_pendiente(200_000))

    def test_pendiente_uno_arriba_del_tope_no_entra(self):
        self.assertIsNone(self._crear_pendiente(200_001))

    def test_pendiente_uno_abajo_del_tope_entra(self):
        self.assertIsNotNone(self._crear_pendiente(199_999))

    def test_segundo_pendiente_que_no_entra_en_el_saldo_es_rechazado(self):
        self._crear_pendiente(150_000)
        self.assertIsNone(self._crear_pendiente(150_000, operator=self.otro_operator))

    def test_dos_pendientes_que_entran_en_el_saldo_van_al_mismo_destinatario(self):
        """El candado no aparta la cuenta entera, aparta el monto: si hay cupo para
        los dos, los dos van a la misma cuenta."""
        primero = self._crear_pendiente(50_000)
        segundo = self._crear_pendiente(50_000, operator=self.otro_operator)
        self.assertIsNotNone(primero)
        self.assertIsNotNone(segundo)
        self.assertEqual(primero.payment_recipient, segundo.payment_recipient)
        self.assertEqual(self.recipient.get_remaining_amount(), 100_000)

    def test_pendiente_de_monto_cero_no_entra(self):
        self.recipient.min_threshold = 1
        self.recipient.save()
        self.assertIsNone(self._crear_pendiente(0))

    # ---- el pendiente saca al destinatario de la asignación automática ----

    def test_el_destinatario_lleno_de_pendientes_deja_de_sugerirse(self):
        segundo = PaymentRecipient.objects.create(
            name="Cuenta secundaria",
            alias="cuenta.secundaria",
            cbu="9876543210987654321098",
            max_amount=200_000,
            priority_order=2,
        )
        self._crear_pendiente(200_000)
        sugerido = PaymentRecipient.objects.find_best_recipient(50_000)
        self.assertEqual(sugerido, segundo)

    def test_sin_cupo_en_ningun_destinatario_no_hay_sugerencia(self):
        self._crear_pendiente(200_000)
        self.assertIsNone(PaymentRecipient.objects.find_best_recipient(50_000))

    # ---- señas ----

    def test_una_seña_pendiente_no_consume_cupo(self):
        deposit_recipient = PaymentRecipient.objects.create(
            name="Cuenta de señas",
            alias="cuenta.de.senias",
            cbu="1111222233334444555566",
            recipient_type=PaymentRecipient.DEPOSIT_ONLY,
            max_amount=1,
            priority_order=2,
        )
        Payment.objects.create(
            amount=500_000,
            payment_type=Payment.DEPOSIT,
            estado=Payment.PENDIENTE,
            payment_recipient=deposit_recipient,
            patient_name="Paciente Ficticio",
            professional=self.professional,
            operator_user=self.operator,
        )
        self.assertTrue(deposit_recipient.can_receive_amount(999_999))


class CupoSemanalConPendientesTests(BaseTestCase):
    """El tope semanal cuenta pendientes igual que el mensual."""

    def setUp(self):
        self.operator = User.objects.create_user(
            username="operador", email="operador@example.com", password="x", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="profesional",
            email="profesional@example.com",
            password="x",
            role=User.PROFESSIONAL,
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Cuenta con tope semanal",
            alias="cuenta.semanal",
            cbu="1234567890123456789012",
            max_amount=500_000,
            weekly_cap_amount=100_000,
            priority_order=1,
        )

    def _crear_pendiente(self, amount):
        return Payment.objects.crear_pendiente(
            payment_recipient=self.recipient,
            amount=amount,
            operator_user=self.operator,
            patient_name="Paciente Ficticio",
            professional=self.professional,
        )

    def test_un_pendiente_consume_cupo_semanal(self):
        self._crear_pendiente(60_000)
        self.assertEqual(self.recipient.get_current_week_received(), 60_000)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 40_000)

    def test_pendiente_justo_en_el_tope_semanal_entra(self):
        self.assertIsNotNone(self._crear_pendiente(100_000))

    def test_pendiente_uno_arriba_del_tope_semanal_no_entra(self):
        self.assertIsNone(self._crear_pendiente(100_001))

    def test_un_cancelado_libera_el_cupo_semanal(self):
        payment = self._crear_pendiente(100_000)
        payment.cancelar(motivo=Payment.ERROR_DE_CARGA, usuario=self.operator)
        self.assertEqual(self.recipient.get_current_week_received(), 0)
        self.assertIsNotNone(self._crear_pendiente(100_000))


class DineroRecibidoVsCupoTests(BaseTestCase):
    """Cupo y plata cobrada son dos preguntas distintas y dan números distintos.

    El cupo cuenta pendientes (ya ocupan la cuenta). Los reportes de dinero cuentan
    solo completados (lo demás todavía no entró, o no va a entrar nunca)."""

    def setUp(self):
        self.operator = User.objects.create_user(
            username="operador", email="operador@example.com", password="x", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="profesional",
            email="profesional@example.com",
            password="x",
            role=User.PROFESSIONAL,
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Cuenta principal",
            alias="cuenta.principal",
            cbu="1234567890123456789012",
            max_amount=500_000,
            priority_order=1,
        )

    def test_un_pendiente_ocupa_cupo_pero_no_cuenta_como_plata_cobrada(self):
        Payment.objects.crear_pendiente(
            payment_recipient=self.recipient,
            amount=70_000,
            operator_user=self.operator,
            patient_name="Paciente Ficticio",
            professional=self.professional,
        )
        self.assertEqual(self.recipient.get_current_month_received(), 70_000)
        self.assertEqual(self.professional.get_current_month_amount(), 0)
        self.assertEqual(Payment.get_monthly_totals()["total_amount"], 0)

    def test_al_completarse_pasa_a_contar_como_plata_cobrada(self):
        payment = Payment.objects.crear_pendiente(
            payment_recipient=self.recipient,
            amount=70_000,
            operator_user=self.operator,
            patient_name="Paciente Ficticio",
            professional=self.professional,
        )
        payment.completar(
            SimpleUploadedFile("comprobante.png", b"contenido", content_type="image/png")
        )
        self.assertEqual(self.professional.get_current_month_amount(), 70_000)
        self.assertEqual(Payment.get_monthly_totals()["total_amount"], 70_000)

    def test_un_cancelado_no_cuenta_en_ningun_lado(self):
        payment = Payment.objects.crear_pendiente(
            payment_recipient=self.recipient,
            amount=70_000,
            operator_user=self.operator,
            patient_name="Paciente Ficticio",
            professional=self.professional,
        )
        payment.cancelar(motivo=Payment.PACIENTE_NO_PAGO, usuario=self.operator)
        self.assertEqual(self.recipient.get_current_month_received(), 0)
        self.assertEqual(self.professional.get_current_month_amount(), 0)
        self.assertEqual(Payment.get_monthly_totals()["total_amount"], 0)


class FallbackDeDestinatarioTests(BaseTestCase):
    """Cuando otro operador se lleva el cupo entre la sugerencia y el lock, el pago
    sigue por el orden de prioridad en vez de fallar.

    Sin esto, con varias operadoras a la vez contra un destinatario casi lleno, la
    segunda recibe un error donde el siguiente por prioridad tenía cupo de sobra."""

    def setUp(self):
        self.client = Client()
        self.password = "pass12345"
        self.operator = User.objects.create_user(
            username="op", email="op@example.com", password=self.password, role=User.OPERATOR
        )
        self.otro_operator = User.objects.create_user(
            username="otro", email="otro@example.com", password=self.password, role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="prof",
            email="prof@example.com",
            password=self.password,
            role=User.PROFESSIONAL,
        )
        self.primero = PaymentRecipient.objects.create(
            name="Prioridad uno",
            alias="prioridad.uno",
            cbu="1234567890123456789012",
            max_amount=100_000,
            priority_order=1,
        )
        self.segundo = PaymentRecipient.objects.create(
            name="Prioridad dos",
            alias="prioridad.dos",
            cbu="9876543210987654321098",
            max_amount=100_000,
            priority_order=2,
        )
        self.client.login(username="op", password=self.password)

    def test_si_el_primero_se_llena_el_pago_va_al_siguiente(self):
        # Deja al de prioridad 1 sin lugar para otro pago de 60.000.
        Payment.objects.crear_pendiente(
            payment_recipient=self.primero,
            amount=60_000,
            operator_user=self.otro_operator,
            patient_name="Paciente Ficticio",
            professional=self.professional,
        )
        resp = self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(
                {
                    "amount": 60_000,
                    "patient_name": "Paciente Ficticio",
                    "professional_id": self.professional.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["alias"], "prioridad.dos")

    def test_si_ninguno_tiene_cupo_devuelve_error(self):
        for recipient in (self.primero, self.segundo):
            Payment.objects.crear_pendiente(
                payment_recipient=recipient,
                amount=100_000,
                operator_user=self.otro_operator,
                patient_name="Paciente Ficticio",
                professional=self.professional,
            )
        resp = self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(
                {
                    "amount": 60_000,
                    "patient_name": "Paciente Ficticio",
                    "professional_id": self.professional.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No hay destinatarios disponibles", resp.json()["error"])
