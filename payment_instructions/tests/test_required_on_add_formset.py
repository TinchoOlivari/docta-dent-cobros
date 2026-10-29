from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from payment_instructions.admin import (
    PosnetPaymentDetailInlineOnAdd,
    TarjetaPaymentDetailInlineOnAdd,
)
from payment_instructions.models import (
    Banco,
    CuentaPosnet,
    Payment,
    Posnet,
    TarjetaPaymentDetail,
    User,
)
from payment_instructions.tests import BaseTestCase


class RequiredOnAddFormSetTestMixin(BaseTestCase):
    """_TarjetaRequiredOnAddFormSet y _PosnetRequiredOnAddFormSet son el formset
    que se usa en el alta nueva del admin (admin.py): exigen la fila del detalle
    solo cuando el método elegido la necesita. Se prueban armando el formset
    directo, sin pasar por una request HTTP completa.

    El usuario tiene que ser superusuario: el has_changed() que arma Django para
    un inline (DeleteProtectedModelForm, en contrib.admin.options) devuelve False
    a propósito si el usuario no tiene permiso de alta sobre TarjetaPaymentDetail/
    PosnetPaymentDetail — sin eso, cualquier dato cargado en la fila se trata
    como "sin cambios" y el formset queda vacío igual."""

    def setUp(self):
        self.admin_site = AdminSite()
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )
        self.professional_user = User.objects.create_user(
            username="professional", email="professional@example.com", role=User.PROFESSIONAL
        )
        self.banco = Banco.objects.create(nombre="Banco de prueba")
        self.cuenta = CuentaPosnet.objects.create(nombre="Cuenta de prueba")
        self.posnet = Posnet.objects.create(
            nombre="Posnet de prueba",
            proveedor=Posnet.MERCADOPAGO,
            cuenta_actual=self.cuenta,
        )

    def _request(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        return request

    def _payment_sin_guardar(self, metodo):
        return Payment(
            amount=100,
            metodo=metodo,
            professional=self.professional_user,
            operator_user=self.admin_user,
            patient_name="Paciente de ejemplo",
        )

    def _build_formset(self, inline_class, payment, data):
        inline = inline_class(Payment, self.admin_site)
        FormSet = inline.get_formset(self._request(), obj=None)
        prefix = FormSet.get_default_prefix()
        management_data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1",
        }
        full_data = {**management_data, **{f"{prefix}-0-{k}": v for k, v in data.items()}}
        return FormSet(data=full_data, instance=payment, prefix=prefix)

    def _build_formset_sin_filas(self, inline_class, payment):
        # El caso normal ahora: extra=0, el administrador nunca hizo clic en
        # "Agregar otro", TOTAL_FORMS llega en 0.
        inline = inline_class(Payment, self.admin_site)
        FormSet = inline.get_formset(self._request(), obj=None)
        prefix = FormSet.get_default_prefix()
        data = {
            f"{prefix}-TOTAL_FORMS": "0",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1",
        }
        return FormSet(data=data, instance=payment, prefix=prefix)


class TarjetaRequiredOnAddFormSetTests(RequiredOnAddFormSetTestMixin):
    CAMPOS = (
        "tipo_tarjeta",
        "banco",
        "ultimos_4_digitos",
        "posnet",
        "cuenta",
        "numero_operacion",
        "monto_cobrado",
    )

    def _detalle_data(self, **overrides):
        data = {
            "tipo_tarjeta": TarjetaPaymentDetail.CREDITO,
            "banco": str(self.banco.id),
            "ultimos_4_digitos": "1234",
            "posnet": str(self.posnet.id),
            "cuenta": str(self.cuenta.id),
            "numero_operacion": "OP-1",
            "monto_cobrado": "100",
        }
        data.update(overrides)
        return data

    def test_fila_vacia_con_metodo_tarjeta_es_invalida(self):
        payment = self._payment_sin_guardar(Payment.POSNET_TARJETA)
        formset = self._build_formset(
            TarjetaPaymentDetailInlineOnAdd,
            payment,
            self._detalle_data(**dict.fromkeys(self.CAMPOS, "")),
        )
        self.assertFalse(formset.is_valid())
        self.assertIn("necesita", str(formset.non_form_errors()))

    def test_sin_ninguna_fila_con_metodo_tarjeta_es_invalida(self):
        # Caso normal ahora: extra=0, nadie clickeó "Agregar otro".
        payment = self._payment_sin_guardar(Payment.POSNET_TARJETA)
        formset = self._build_formset_sin_filas(TarjetaPaymentDetailInlineOnAdd, payment)
        self.assertFalse(formset.is_valid())
        self.assertIn("necesita", str(formset.non_form_errors()))

    def test_fila_completa_con_metodo_tarjeta_es_valida(self):
        payment = self._payment_sin_guardar(Payment.POSNET_TARJETA)
        formset = self._build_formset(
            TarjetaPaymentDetailInlineOnAdd, payment, self._detalle_data()
        )
        self.assertTrue(formset.is_valid())

    def test_fila_vacia_con_otro_metodo_no_se_exige(self):
        payment = self._payment_sin_guardar(Payment.EFECTIVO)
        formset = self._build_formset(
            TarjetaPaymentDetailInlineOnAdd,
            payment,
            self._detalle_data(**dict.fromkeys(self.CAMPOS, "")),
        )
        self.assertTrue(formset.is_valid())


class PosnetRequiredOnAddFormSetTests(RequiredOnAddFormSetTestMixin):
    CAMPOS = ("posnet", "cuenta", "numero_operacion", "monto_cobrado")

    def _detalle_data(self, **overrides):
        data = {
            "posnet": str(self.posnet.id),
            "cuenta": str(self.cuenta.id),
            "numero_operacion": "OP-1",
            "monto_cobrado": "100",
        }
        data.update(overrides)
        return data

    def test_fila_vacia_con_metodo_qr_es_invalida(self):
        payment = self._payment_sin_guardar(Payment.POSNET_QR)
        formset = self._build_formset(
            PosnetPaymentDetailInlineOnAdd,
            payment,
            self._detalle_data(**dict.fromkeys(self.CAMPOS, "")),
        )
        self.assertFalse(formset.is_valid())
        self.assertIn("necesita", str(formset.non_form_errors()))

    def test_fila_vacia_con_metodo_link_pago_es_invalida(self):
        payment = self._payment_sin_guardar(Payment.LINK_PAGO)
        formset = self._build_formset(
            PosnetPaymentDetailInlineOnAdd,
            payment,
            self._detalle_data(**dict.fromkeys(self.CAMPOS, "")),
        )
        self.assertFalse(formset.is_valid())
        self.assertIn("necesita", str(formset.non_form_errors()))

    def test_sin_ninguna_fila_con_metodo_qr_es_invalida(self):
        payment = self._payment_sin_guardar(Payment.POSNET_QR)
        formset = self._build_formset_sin_filas(PosnetPaymentDetailInlineOnAdd, payment)
        self.assertFalse(formset.is_valid())
        self.assertIn("necesita", str(formset.non_form_errors()))

    def test_fila_completa_con_metodo_qr_es_valida(self):
        payment = self._payment_sin_guardar(Payment.POSNET_QR)
        formset = self._build_formset(PosnetPaymentDetailInlineOnAdd, payment, self._detalle_data())
        self.assertTrue(formset.is_valid())

    def test_fila_vacia_con_otro_metodo_no_se_exige(self):
        payment = self._payment_sin_guardar(Payment.EFECTIVO)
        formset = self._build_formset(
            PosnetPaymentDetailInlineOnAdd,
            payment,
            self._detalle_data(**dict.fromkeys(self.CAMPOS, "")),
        )
        self.assertTrue(formset.is_valid())
