from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.test import RequestFactory
from django.utils import timezone

from payment_instructions.admin import (
    BancoAdmin,
    CuentaPosnetAdmin,
    PaymentAdmin,
    PosnetAdmin,
    PosnetPaymentDetailInlineOnAdd,
    TarjetaPaymentDetailInline,
    TarjetaPaymentDetailInlineOnAdd,
)
from payment_instructions.models import (
    Banco,
    CuentaPosnet,
    Payment,
    PaymentRecipient,
    Posnet,
    TarjetaPaymentDetail,
)
from payment_instructions.tests import BaseTestCase

User = get_user_model()


class AdminOnlyModulePermissionTests(BaseTestCase):
    """Banco, Posnet y CuentaPosnet son backoffice exclusivo del administrador.
    El operador nunca entra a gestionarlos, solo los elige por nombre desde su
    propio formulario."""

    def setUp(self):
        self.admin_site = AdminSite()
        self.banco_admin = BancoAdmin(Banco, self.admin_site)
        self.posnet_admin = PosnetAdmin(Posnet, self.admin_site)
        self.cuenta_admin = CuentaPosnetAdmin(CuentaPosnet, self.admin_site)

        self.admin_user = User.objects.create_user(
            username="admin", email="admin@example.com", role=User.ADMINISTRATOR, is_staff=True
        )
        self.operator_user = User.objects.create_user(
            username="operator", email="operator@example.com", role=User.OPERATOR, is_staff=True
        )
        self.superuser = User.objects.create_superuser(
            username="super", email="super@example.com", password="pass"
        )

    def _request(self, user):
        request = HttpRequest()
        request.user = user
        return request

    def test_operator_has_no_module_permission(self):
        request = self._request(self.operator_user)
        self.assertFalse(self.banco_admin.has_module_permission(request))
        self.assertFalse(self.posnet_admin.has_module_permission(request))
        self.assertFalse(self.cuenta_admin.has_module_permission(request))

    def test_administrator_has_module_permission(self):
        request = self._request(self.admin_user)
        self.assertTrue(self.banco_admin.has_module_permission(request))
        self.assertTrue(self.posnet_admin.has_module_permission(request))
        self.assertTrue(self.cuenta_admin.has_module_permission(request))

    def test_superuser_has_module_permission(self):
        request = self._request(self.superuser)
        self.assertTrue(self.banco_admin.has_module_permission(request))
        self.assertTrue(self.posnet_admin.has_module_permission(request))
        self.assertTrue(self.cuenta_admin.has_module_permission(request))


class CuentaPosnetReiniciarAcumuladoTests(BaseTestCase):
    def setUp(self):
        self.admin_site = AdminSite()
        self.cuenta_admin = CuentaPosnetAdmin(CuentaPosnet, self.admin_site)
        self.admin_user = User.objects.create_user(
            username="admin", email="admin@example.com", role=User.ADMINISTRATOR, is_staff=True
        )

    def test_reiniciar_acumulado_pone_la_fecha_en_el_presente(self):
        vieja = timezone.now() - timedelta(days=30)
        cuenta = CuentaPosnet.objects.create(nombre="Cuenta 1", acumulado_desde=vieja)

        request = RequestFactory().get("/")
        request.user = self.admin_user
        # setUp de RequestFactory no arma el middleware de mensajes; message_user
        # lo necesita para agregar el mensaje de éxito.
        from django.contrib.messages.storage.fallback import FallbackStorage

        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        queryset = CuentaPosnet.objects.filter(pk=cuenta.pk)
        self.cuenta_admin.reiniciar_acumulado(request, queryset)

        cuenta.refresh_from_db()
        self.assertAlmostEqual(cuenta.acumulado_desde, timezone.now(), delta=timedelta(seconds=10))


class PaymentAdminInlineTests(BaseTestCase):
    """El inline de tarjeta solo aparece para editar un pago de tarjeta ya
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
        self.recipient = PaymentRecipient.objects.create(
            name="Destinatario",
            alias="destinatario",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )

    def _request(self):
        request = HttpRequest()
        request.user = self.admin_user
        return request

    def test_get_inline_instances_sin_objeto_muestra_los_dos_para_completar(self):
        # Alta nueva: no se sabe todavía qué método va a elegir el administrador,
        # así que se muestran los dos inlines con una fila vacía cada uno.
        instances = self.payment_admin.get_inline_instances(self._request(), obj=None)
        self.assertEqual(len(instances), 2)
        self.assertIsInstance(instances[0], TarjetaPaymentDetailInlineOnAdd)
        self.assertIsInstance(instances[1], PosnetPaymentDetailInlineOnAdd)

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

    def test_get_inline_instances_con_tarjeta_muestra_inline(self):
        payment = Payment.objects.create(
            amount=100,
            metodo=Payment.POSNET_TARJETA,
            professional=self.professional_user,
            operator_user=self.operator_user,
            patient_name="Paciente de ejemplo",
        )
        banco = Banco.objects.create(nombre="Banco de ejemplo")
        cuenta = CuentaPosnet.objects.create(nombre="Cuenta de ejemplo")
        posnet = Posnet.objects.create(
            nombre="Posnet de ejemplo", proveedor=Posnet.MERCADOPAGO, cuenta_actual=cuenta
        )
        TarjetaPaymentDetail.objects.create(
            payment=payment,
            tipo_tarjeta=TarjetaPaymentDetail.CREDITO,
            banco=banco,
            ultimos_4_digitos="1234",
            posnet=posnet,
            cuenta=cuenta,
            numero_operacion="OP-1",
            monto_cobrado=100,
        )

        instances = self.payment_admin.get_inline_instances(self._request(), obj=payment)
        self.assertEqual(len(instances), 1)
        self.assertIsInstance(instances[0], TarjetaPaymentDetailInline)


class PaymentAdminSaveModelTarjetaTests(BaseTestCase):
    """Cambiarle el método a un pago que YA existe a tarjeta en la misma edición
    no da forma de cargar el TarjetaPaymentDetail en ese mismo submit (el inline
    que se mostró es el del método anterior). save_model tiene que frenar ese
    caso en vez de guardar un pago de tarjeta sin detalle.

    El alta nueva es distinta: get_inline_instances(obj=None) ya muestra el
    inline con una fila para completar, y su formset
    (_TarjetaRequiredOnAddFormSet, ver test_required_on_add_formset.py) exige
    esa fila antes de que save_model llegue a ejecutarse. save_model ya no
    necesita frenar el alta nueva por su cuenta."""

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

    def test_cambiar_metodo_existente_a_tarjeta_no_guarda(self):
        payment = Payment.objects.create(
            amount=100,
            metodo=Payment.EFECTIVO,
            professional=self.professional_user,
            operator_user=self.admin_user,
            patient_name="Paciente de ejemplo",
        )
        payment.metodo = Payment.POSNET_TARJETA  # como si el form lo hubiera cambiado
        self.payment_admin.save_model(self._request(), payment, form=None, change=True)
        payment.refresh_from_db()
        self.assertEqual(payment.metodo, Payment.EFECTIVO)  # no se guardó el cambio

    def test_editar_tarjeta_existente_con_detalle_guarda(self):
        payment = Payment.objects.create(
            amount=100,
            metodo=Payment.POSNET_TARJETA,
            professional=self.professional_user,
            operator_user=self.admin_user,
            patient_name="Paciente de ejemplo",
        )
        banco = Banco.objects.create(nombre="Banco de ejemplo")
        cuenta = CuentaPosnet.objects.create(nombre="Cuenta de ejemplo")
        posnet = Posnet.objects.create(
            nombre="Posnet de ejemplo", proveedor=Posnet.MERCADOPAGO, cuenta_actual=cuenta
        )
        TarjetaPaymentDetail.objects.create(
            payment=payment,
            tipo_tarjeta=TarjetaPaymentDetail.CREDITO,
            banco=banco,
            ultimos_4_digitos="1234",
            posnet=posnet,
            cuenta=cuenta,
            numero_operacion="OP-1",
            monto_cobrado=100,
        )
        payment.observaciones = "Editado"
        self.payment_admin.save_model(self._request(), payment, form=None, change=True)
        payment.refresh_from_db()
        self.assertEqual(payment.observaciones, "Editado")
