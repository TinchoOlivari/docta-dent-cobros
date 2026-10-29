"""Transiciones de estado de un pago: quién puede pasar de dónde a dónde, y qué datos
exige cada movimiento.

El estado es lo que decide si un pago ocupa cupo (ver test_cupo_bloqueante.py). Acá se
prueba la máquina de estados en sí, sin mirar montos.
"""

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from django.utils import timezone

from payment_instructions.models import Payment, PaymentRecipient, User, get_now_arg
from payment_instructions.tests import BaseTestCase


class PaymentEstadoTests(BaseTestCase):
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
            name="Cuenta de prueba",
            alias="cuenta.de.prueba",
            cbu="1234567890123456789012",
            max_amount=1_000_000,
            priority_order=1,
        )

    def _create_pendiente(self, amount=50_000):
        return Payment.objects.crear_pendiente(
            payment_recipient=self.recipient,
            amount=amount,
            operator_user=self.operator,
            patient_name="Paciente Ficticio",
            professional=self.professional,
        )

    def _proof_file(self):
        return SimpleUploadedFile("comprobante.png", b"contenido", content_type="image/png")

    # ---- creación ----

    def test_crear_pendiente_deja_el_pago_en_pendiente(self):
        payment = self._create_pendiente()
        self.assertEqual(payment.estado, Payment.PENDIENTE)
        self.assertEqual(payment.metodo, Payment.TRANSFERENCIA)
        self.assertFalse(payment.proof_of_payment_file)

    def test_crear_pendiente_devuelve_none_si_el_destinatario_no_puede_recibir(self):
        self.recipient.is_active = False
        self.recipient.save()
        self.assertIsNone(self._create_pendiente())

    # ---- completar ----

    def test_completar_un_pendiente_lo_deja_completado_con_comprobante(self):
        payment = self._create_pendiente()
        payment.completar(self._proof_file())
        payment.refresh_from_db()
        self.assertEqual(payment.estado, Payment.COMPLETADO)
        self.assertTrue(payment.proof_of_payment_file)

    def test_completar_sin_comprobante_falla(self):
        payment = self._create_pendiente()
        with self.assertRaises(ValidationError) as ctx:
            payment.completar(None)
        self.assertIn("proof_of_payment_file", ctx.exception.message_dict)

    def test_completar_un_pago_ya_completado_falla(self):
        payment = self._create_pendiente()
        payment.completar(self._proof_file())
        with self.assertRaises(ValidationError) as ctx:
            payment.completar(self._proof_file())
        self.assertIn("estado", ctx.exception.message_dict)

    def test_completar_un_pago_cancelado_falla(self):
        payment = self._create_pendiente()
        payment.cancelar(motivo=Payment.PACIENTE_NO_PAGO, usuario=self.operator)
        with self.assertRaises(ValidationError) as ctx:
            payment.completar(self._proof_file())
        self.assertIn("estado", ctx.exception.message_dict)

    # ---- cancelar ----

    def test_cancelar_deja_rastro_de_quien_y_por_que(self):
        payment = self._create_pendiente()
        payment.cancelar(motivo=Payment.PACIENTE_NO_PAGO, usuario=self.operator)
        payment.refresh_from_db()
        self.assertEqual(payment.estado, Payment.CANCELADO)
        self.assertEqual(payment.motivo_cancelacion, Payment.PACIENTE_NO_PAGO)
        self.assertEqual(payment.cancelado_por, self.operator)
        self.assertIsNotNone(payment.cancelado_at)

    def test_cancelar_no_borra_el_registro(self):
        payment = self._create_pendiente()
        payment.cancelar(motivo=Payment.ERROR_DE_CARGA, usuario=self.operator)
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())

    def test_cancelar_un_completado_es_valido(self):
        """La anulación de un pago ya cobrado existe: es la única salida cuando se
        cargó mal algo que no se puede borrar. Quién puede hacerlo lo decide la view."""
        payment = self._create_pendiente()
        payment.completar(self._proof_file())
        payment.cancelar(motivo=Payment.ERROR_DE_CARGA, usuario=self.operator)
        payment.refresh_from_db()
        self.assertEqual(payment.estado, Payment.CANCELADO)

    def test_cancelar_dos_veces_falla(self):
        payment = self._create_pendiente()
        payment.cancelar(motivo=Payment.ERROR_DE_CARGA, usuario=self.operator)
        with self.assertRaises(ValidationError) as ctx:
            payment.cancelar(motivo=Payment.ERROR_DE_CARGA, usuario=self.operator)
        self.assertIn("estado", ctx.exception.message_dict)

    def test_cancelar_con_motivo_invalido_falla(self):
        payment = self._create_pendiente()
        with self.assertRaises(ValidationError) as ctx:
            payment.cancelar(motivo="INVENTADO", usuario=self.operator)
        self.assertIn("motivo_cancelacion", ctx.exception.message_dict)

    def test_cancelar_sin_motivo_falla(self):
        payment = self._create_pendiente()
        with self.assertRaises(ValidationError) as ctx:
            payment.cancelar(motivo=None, usuario=self.operator)
        self.assertIn("motivo_cancelacion", ctx.exception.message_dict)

    def test_cancelar_con_motivo_otro_sin_detalle_falla(self):
        payment = self._create_pendiente()
        with self.assertRaises(ValidationError) as ctx:
            payment.cancelar(motivo=Payment.OTRO, usuario=self.operator)
        self.assertIn("observacion_cancelacion", ctx.exception.message_dict)

    def test_cancelar_con_motivo_otro_y_detalle_en_blanco_falla(self):
        payment = self._create_pendiente()
        with self.assertRaises(ValidationError) as ctx:
            payment.cancelar(motivo=Payment.OTRO, usuario=self.operator, observacion="   ")
        self.assertIn("observacion_cancelacion", ctx.exception.message_dict)

    def test_cancelar_con_motivo_otro_y_detalle_funciona(self):
        payment = self._create_pendiente()
        payment.cancelar(
            motivo=Payment.OTRO, usuario=self.operator, observacion="El paciente se retiró."
        )
        payment.refresh_from_db()
        self.assertEqual(payment.observacion_cancelacion, "El paciente se retiró.")


class EstadoInicialPorMetodoTests(BaseTestCase):
    """Qué estado le corresponde a cada método de cobro cuando el pago nace."""

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

    def test_efectivo_no_puede_quedar_pendiente(self):
        """La plata está en la mano: no hay comprobante que esperar."""
        payment = Payment(
            amount=10_000,
            payment_type=Payment.REGULAR,
            metodo=Payment.EFECTIVO,
            estado=Payment.PENDIENTE,
            patient_name="Paciente Ficticio",
            professional=self.professional,
            operator_user=self.operator,
        )
        with self.assertRaises(ValidationError) as ctx:
            payment.clean()
        self.assertIn("estado", ctx.exception.message_dict)

    def test_efectivo_completado_es_valido(self):
        payment = Payment(
            amount=10_000,
            payment_type=Payment.REGULAR,
            metodo=Payment.EFECTIVO,
            estado=Payment.COMPLETADO,
            patient_name="Paciente Ficticio",
            professional=self.professional,
            operator_user=self.operator,
        )
        payment.clean()  # no levanta

    def test_un_pago_cancelado_necesita_motivo(self):
        payment = Payment(
            amount=10_000,
            payment_type=Payment.REGULAR,
            metodo=Payment.EFECTIVO,
            estado=Payment.CANCELADO,
            patient_name="Paciente Ficticio",
            professional=self.professional,
            operator_user=self.operator,
        )
        with self.assertRaises(ValidationError) as ctx:
            payment.clean()
        self.assertIn("motivo_cancelacion", ctx.exception.message_dict)


class TotalMensualDelProfesionalTests(BaseTestCase):
    """El total mensual del profesional se corta en hora Argentina.

    Con timezone.now() a secas, un pago de las 21:00 del último día del mes ya cae en
    el mes siguiente en UTC y se lo cuenta al período equivocado. Solo se ve de noche
    a fin de mes, que es justo cuando nadie lo está mirando."""

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
            max_amount=1_000_000,
            priority_order=1,
        )

    def _pago(self, created_at, estado=Payment.COMPLETADO):
        return Payment.objects.create(
            amount=10_000,
            payment_type=Payment.REGULAR,
            metodo=Payment.TRANSFERENCIA,
            estado=estado,
            payment_recipient=self.recipient,
            patient_name="Paciente Ficticio",
            professional=self.professional,
            operator_user=self.operator,
            created_at=created_at,
        )

    def test_un_pago_del_primer_dia_a_las_cero_cuenta_en_el_mes(self):
        now_arg = get_now_arg()
        primer_dia = now_arg.replace(day=1, hour=0, minute=1, second=0, microsecond=0)
        self._pago(primer_dia)
        self.assertEqual(self.professional.get_current_month_amount(), 10_000)

    def test_un_pago_de_la_ultima_hora_del_mes_anterior_no_cuenta(self):
        now_arg = get_now_arg()
        inicio_mes = now_arg.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ultimo_instante_mes_anterior = inicio_mes - timezone.timedelta(minutes=1)
        self._pago(ultimo_instante_mes_anterior)
        self.assertEqual(self.professional.get_current_month_amount(), 0)

    def test_un_pendiente_no_cuenta_en_el_total_del_profesional(self):
        self._pago(get_now_arg(), estado=Payment.PENDIENTE)
        self.assertEqual(self.professional.get_current_month_amount(), 0)
