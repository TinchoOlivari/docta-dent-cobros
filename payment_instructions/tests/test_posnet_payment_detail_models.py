import itertools

from django.core.exceptions import ValidationError

from payment_instructions.models import (
    CuentaPosnet,
    Payment,
    Posnet,
    PosnetPaymentDetail,
    TarjetaPaymentDetail,
    User,
)
from payment_instructions.tests import BaseTestCase


class PosnetPaymentDetailTestMixin(BaseTestCase):
    """Helpers comunes para crear posnets, cuentas y pagos QR/link de pago."""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="prof@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self._seq = itertools.count(1)

    def _create_posnet(self, is_active=True, cuenta_actual=None, nombre=None):
        nombre = nombre or f"Posnet {next(self._seq)}"
        return Posnet.objects.create(
            nombre=nombre,
            proveedor=Posnet.MERCADOPAGO,
            is_active=is_active,
            cuenta_actual=cuenta_actual,
        )

    def _create_cuenta(self, objetivo_monto=0, nombre=None):
        nombre = nombre or f"Cuenta {next(self._seq)}"
        return CuentaPosnet.objects.create(nombre=nombre, objetivo_monto=objetivo_monto)

    def _create_payment_posnet(
        self, metodo=Payment.POSNET_QR, amount=1000, estado=Payment.COMPLETADO
    ):
        """Payment.objects.create() no llama a clean(): se usa para armar el estado
        exacto que necesita cada caso, igual que el resto de los tests del repo."""
        return Payment.objects.create(
            metodo=metodo,
            payment_type=Payment.REGULAR,
            amount=amount,
            professional=self.professional,
            operator_user=self.operator,
            patient_name="Paciente de Prueba",
            estado=estado,
        )


class PosnetPaymentDetailCleanTests(PosnetPaymentDetailTestMixin):
    def _build_valid_detail(self, metodo=Payment.POSNET_QR, **overrides):
        payment = overrides.pop("payment", None) or self._create_payment_posnet(
            metodo=metodo, amount=1000
        )
        posnet = overrides.pop("posnet", None) or self._create_posnet()
        cuenta = overrides.pop("cuenta", None) or self._create_cuenta()
        defaults = {
            "payment": payment,
            "posnet": posnet,
            "cuenta": cuenta,
            "numero_operacion": "OP1",
            "monto_cobrado": 1000,
        }
        defaults.update(overrides)
        return PosnetPaymentDetail(**defaults)

    def test_caso_valido_con_qr_no_rechaza(self):
        detalle = self._build_valid_detail(metodo=Payment.POSNET_QR)
        detalle.full_clean()

    def test_caso_valido_con_link_pago_no_rechaza(self):
        detalle = self._build_valid_detail(metodo=Payment.LINK_PAGO)
        detalle.full_clean()

    def test_posnet_inactivo_rechaza(self):
        posnet_inactivo = self._create_posnet(is_active=False)
        detalle = self._build_valid_detail(posnet=posnet_inactivo)
        with self.assertRaises(ValidationError) as cm:
            detalle.full_clean()
        self.assertIn("posnet", cm.exception.message_dict)

    def test_numero_operacion_vacio_rechaza_con_clean_directo(self):
        detalle = self._build_valid_detail(numero_operacion="")
        with self.assertRaises(ValidationError) as cm:
            detalle.clean()
        self.assertIn("numero_operacion", cm.exception.message_dict)

    def test_monto_cobrado_menor_al_monto_del_pago_rechaza(self):
        payment = self._create_payment_posnet(amount=1000)
        detalle = self._build_valid_detail(payment=payment, monto_cobrado=999)
        with self.assertRaises(ValidationError) as cm:
            detalle.full_clean()
        self.assertIn("monto_cobrado", cm.exception.message_dict)

    def test_monto_cobrado_igual_al_monto_del_pago_no_rechaza(self):
        payment = self._create_payment_posnet(amount=1000)
        detalle = self._build_valid_detail(payment=payment, monto_cobrado=1000)
        detalle.full_clean()


class CuentaPosnetAcumuladoConTarjetaYPosnetTests(PosnetPaymentDetailTestMixin):
    """get_acumulado_recibido() suma detalle_tarjeta + detalle_posnet: una cuenta
    de posnet cobra por los tres métodos indistintamente."""

    def _create_detalle_posnet(self, payment, posnet, cuenta, monto_cobrado=None):
        return PosnetPaymentDetail.objects.create(
            payment=payment,
            posnet=posnet,
            cuenta=cuenta,
            numero_operacion=f"OP{payment.pk}",
            monto_cobrado=monto_cobrado if monto_cobrado is not None else payment.amount,
        )

    def _create_detalle_tarjeta(self, payment, posnet, cuenta):
        from payment_instructions.models import Banco

        banco = Banco.objects.create(nombre=f"Banco {next(self._seq)}")
        return TarjetaPaymentDetail.objects.create(
            payment=payment,
            tipo_tarjeta=TarjetaPaymentDetail.CREDITO,
            banco=banco,
            ultimos_4_digitos="1234",
            posnet=posnet,
            cuenta=cuenta,
            numero_operacion=f"OP{payment.pk}",
            monto_cobrado=payment.amount,
        )

    def test_suma_tarjeta_qr_y_link_pago(self):
        cuenta = self._create_cuenta()
        posnet = self._create_posnet(cuenta_actual=cuenta)

        pago_tarjeta = self._create_payment_posnet(metodo=Payment.POSNET_TARJETA, amount=300)
        self._create_detalle_tarjeta(pago_tarjeta, posnet, cuenta)

        pago_qr = self._create_payment_posnet(metodo=Payment.POSNET_QR, amount=200)
        self._create_detalle_posnet(pago_qr, posnet, cuenta)

        pago_link = self._create_payment_posnet(metodo=Payment.LINK_PAGO, amount=100)
        self._create_detalle_posnet(pago_link, posnet, cuenta)

        self.assertEqual(cuenta.get_acumulado_recibido(), 600)

    def test_pago_qr_cancelado_no_cuenta(self):
        cuenta = self._create_cuenta()
        posnet = self._create_posnet(cuenta_actual=cuenta)

        pago_completado = self._create_payment_posnet(
            metodo=Payment.POSNET_QR, amount=300, estado=Payment.COMPLETADO
        )
        self._create_detalle_posnet(pago_completado, posnet, cuenta)

        pago_cancelado = self._create_payment_posnet(
            metodo=Payment.LINK_PAGO, amount=700, estado=Payment.CANCELADO
        )
        self._create_detalle_posnet(pago_cancelado, posnet, cuenta)

        self.assertEqual(cuenta.get_acumulado_recibido(), 300)
