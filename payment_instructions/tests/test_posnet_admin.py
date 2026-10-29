from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.test import RequestFactory

from payment_instructions.admin import PaymentAdmin, PosnetPaymentDetailInline
from payment_instructions.models import CuentaPosnet, Payment, Posnet, PosnetPaymentDetail
from payment_instructions.tests import BaseTestCase

User = get_user_model()


class PaymentAdminPosnetInlineTests(BaseTestCase):
    """El inline de QR/link solo aparece para editar un pago de ese método ya
    cargado: no en alta nueva, ni en un pago de otro método."""

    def setUp(self):
        self.admin_site = AdminSite()
        self.payment_admin = PaymentAdmin(Payment, self.admin_site)

        self.admin_user = User.objects.create_user(
            username="admin", email="admin@example.com", role=User.ADMINISTRATOR, is_staff=True
        )
        self.operator_user = User.objects.create_user(
            username="operator", email="operator@example.com", role=User.OPERATOR
        )
        self.professional_user = User.objects.create_user(
            username="professional", email="professional@example.com", role=User.PROFESSIONAL
        )

    def _request(self):
        request = HttpRequest()
        request.user = self.admin_user
        return request

    def _create_detalle_posnet(self, payment):
        cuenta = CuentaPosnet.objects.create(nombre="Cuenta de ejemplo")
        posnet = Posnet.objects.create(
            nombre="Posnet de ejemplo", proveedor=Posnet.MERCADOPAGO, cuenta_actual=cuenta
        )
        return PosnetPaymentDetail.objects.create(
            payment=payment,
            posnet=posnet,
            cuenta=cuenta,
            numero_operacion="OP-1",
            monto_cobrado=payment.amount,
        )

    def test_get_inline_instances_con_efectivo_no_muestra_inline(self):
        payment = Payment.objects.create(
            amount=100,
            metodo=Payment.EFECTIVO,
            professional=self.professional_user,
            operator_user=self.operator_user,
            patient_name="Paciente de ejemplo",
        )
        instances = self.payment_admin.get_inline_instances(self._request(), obj=payment)
        self.assertEqual(instances, [])

    def test_get_inline_instances_con_qr_muestra_inline(self):
        payment = Payment.objects.create(
            amount=100,
            metodo=Payment.POSNET_QR,
            professional=self.professional_user,
            operator_user=self.operator_user,
            patient_name="Paciente de ejemplo",
        )
        self._create_detalle_posnet(payment)

        instances = self.payment_admin.get_inline_instances(self._request(), obj=payment)
        self.assertEqual(len(instances), 1)
        self.assertIsInstance(instances[0], PosnetPaymentDetailInline)

    def test_get_inline_instances_con_link_pago_muestra_inline(self):
        payment = Payment.objects.create(
            amount=100,
            metodo=Payment.LINK_PAGO,
            professional=self.professional_user,
            operator_user=self.operator_user,
            patient_name="Paciente de ejemplo",
        )
        self._create_detalle_posnet(payment)

        instances = self.payment_admin.get_inline_instances(self._request(), obj=payment)
        self.assertEqual(len(instances), 1)
        self.assertIsInstance(instances[0], PosnetPaymentDetailInline)


class PaymentAdminSaveModelPosnetTests(BaseTestCase):
    """Cambiarle el método a un pago que YA existe a QR/link en la misma edición
    no da forma de cargar el PosnetPaymentDetail en ese mismo submit. save_model
    tiene que frenar ese caso en vez de guardar un pago sin detalle.

    El alta nueva la cubre el formset del inline (ver
    test_required_on_add_formset.py), no save_model: get_inline_instances(obj=None)
    ya muestra el inline con una fila para completar antes de llegar acá."""

    def setUp(self):
        self.admin_site = AdminSite()
        self.payment_admin = PaymentAdmin(Payment, self.admin_site)
        self.admin_user = User.objects.create_user(
            username="admin", email="admin@example.com", role=User.ADMINISTRATOR, is_staff=True
        )
        self.professional_user = User.objects.create_user(
            username="professional", email="professional@example.com", role=User.PROFESSIONAL
        )

    def _request(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        from django.contrib.messages.storage.fallback import FallbackStorage

        setattr(request, "session", "session")
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_cambiar_metodo_existente_a_link_pago_no_guarda(self):
        payment = Payment.objects.create(
            amount=100,
            metodo=Payment.EFECTIVO,
            professional=self.professional_user,
            operator_user=self.admin_user,
            patient_name="Paciente de ejemplo",
        )
        payment.metodo = Payment.LINK_PAGO  # como si el form lo hubiera cambiado
        self.payment_admin.save_model(self._request(), payment, form=None, change=True)
        payment.refresh_from_db()
        self.assertEqual(payment.metodo, Payment.EFECTIVO)  # no se guardó el cambio

    def test_editar_qr_existente_con_detalle_guarda(self):
        payment = Payment.objects.create(
            amount=100,
            metodo=Payment.POSNET_QR,
            professional=self.professional_user,
            operator_user=self.admin_user,
            patient_name="Paciente de ejemplo",
        )
        cuenta = CuentaPosnet.objects.create(nombre="Cuenta de ejemplo")
        posnet = Posnet.objects.create(
            nombre="Posnet de ejemplo", proveedor=Posnet.MERCADOPAGO, cuenta_actual=cuenta
        )
        PosnetPaymentDetail.objects.create(
            payment=payment,
            posnet=posnet,
            cuenta=cuenta,
            numero_operacion="OP-1",
            monto_cobrado=100,
        )
        payment.observaciones = "Editado"
        self.payment_admin.save_model(self._request(), payment, form=None, change=True)
        payment.refresh_from_db()
        self.assertEqual(payment.observaciones, "Editado")
