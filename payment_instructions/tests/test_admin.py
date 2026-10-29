from datetime import timedelta

from django.test import Client, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.sites import AdminSite, site
from django.contrib.messages.storage.base import BaseStorage
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.utils import timezone

from payment_instructions.models import PaymentRecipient, Payment
from payment_instructions.admin import (
    UserAdmin,
    PaymentAdmin,
    PaymentRecipientAdmin,
    OPERATOR_PAYMENT_HISTORY_DAYS,
)
from payment_instructions.tests import BaseTestCase

User = get_user_model()


class ProfessionalAdminPermissionTests(BaseTestCase):
    """Test admin permissions for professional users"""

    def setUp(self):
        self.client = Client()

        # Create users with different roles
        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
        )

        self.operator_user = User.objects.create_user(
            username="operator",
            email="operator@example.com",
            password="pass",
            role=User.OPERATOR,
            is_staff=True,
        )

        self.professional_user = User.objects.create_user(
            username="professional",
            email="professional@example.com",
            password="pass",
            role=User.PROFESSIONAL,
            is_staff=True,
        )

        # Create test data
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test_recipient",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )

    def test_professional_cannot_access_user_admin(self):
        """Test that professionals cannot access User admin"""
        self.client.login(username="professional", password="pass")

        # Try to access user changelist
        resp = self.client.get(reverse("admin:payment_instructions_user_changelist"))
        self.assertEqual(resp.status_code, 403)  # Forbidden

    def test_professional_cannot_access_paymentrecipient_admin(self):
        """Test that professionals cannot access PaymentRecipient admin"""
        self.client.login(username="professional", password="pass")

        # Try to access paymentrecipient changelist
        resp = self.client.get(reverse("admin:payment_instructions_paymentrecipient_changelist"))
        self.assertEqual(resp.status_code, 403)  # Forbidden

    def test_professional_can_access_payment_admin(self):
        """Test that professionals can access Payment admin (but read-only)"""
        self.client.login(username="professional", password="pass")

        # Should be able to access payment changelist
        resp = self.client.get(reverse("admin:payment_instructions_payment_changelist"))
        self.assertEqual(resp.status_code, 200)

    def test_professional_sees_only_their_payments(self):
        """Test that professionals only see payments assigned to them"""
        # Create another professional
        other_professional = User.objects.create_user(
            username="other_professional", email="other@example.com", role=User.PROFESSIONAL
        )

        # Create payments for both professionals
        file1 = SimpleUploadedFile("test1.pdf", b"content1", content_type="application/pdf")
        file2 = SimpleUploadedFile("test2.pdf", b"content2", content_type="application/pdf")

        payment1 = Payment.objects.create(
            amount=100,
            payment_recipient=self.recipient,
            professional=self.professional_user,
            proof_of_payment_file=file1,
            operator_user=self.operator_user,
            patient_name="Test Patient",
        )

        payment2 = Payment.objects.create(
            amount=200,
            payment_recipient=self.recipient,
            professional=other_professional,
            proof_of_payment_file=file2,
            operator_user=self.operator_user,
            patient_name="Other Patient",
        )

        self.client.login(username="professional", password="pass")
        # Directly check the queryset used by the admin
        request = self.client.request().wsgi_request
        # Set user on request
        request.user = self.professional_user
        ma = PaymentAdmin(Payment, site)
        queryset = ma.get_queryset(request)
        ids = set(queryset.values_list("id", flat=True))
        self.assertIn(payment1.id, ids)
        self.assertNotIn(payment2.id, ids)

    def test_professional_cannot_add_payments(self):
        """Test that professionals cannot add new payments"""
        self.client.login(username="professional", password="pass")

        # Try to access payment add form
        resp = self.client.get(reverse("admin:payment_instructions_payment_add"))
        self.assertEqual(resp.status_code, 403)  # Forbidden

    def test_professional_cannot_edit_payments(self):
        """Test that professionals can access the payment change form but cannot edit any fields"""
        file = SimpleUploadedFile("test.pdf", b"content", content_type="application/pdf")
        payment = Payment.objects.create(
            amount=100,
            payment_recipient=self.recipient,
            professional=self.professional_user,
            proof_of_payment_file=file,
            operator_user=self.operator_user,
            patient_name="Test Patient",
        )

        self.client.login(username="professional", password="pass")

        # Try to access payment change form
        resp = self.client.get(
            reverse("admin:payment_instructions_payment_change", args=[payment.id])
        )
        self.assertEqual(resp.status_code, 200)  # Form is accessible

        # Try to POST to change the payment (should not be allowed)
        post_data = {
            "amount": 999,
            "payment_recipient": self.recipient.id,
            "professional": self.professional_user.id,
            "operator_user": self.operator_user.id,
            "proof_of_payment_file": file,
        }
        resp_post = self.client.post(
            reverse("admin:payment_instructions_payment_change", args=[payment.id]),
            post_data,
            follow=True,
        )
        # Should not allow saving, either forbidden or no change
        self.assertEqual(resp_post.status_code, 403)
        payment.refresh_from_db()
        self.assertEqual(payment.amount, 100)

    def test_professional_cannot_delete_payments(self):
        """Test that professionals cannot delete payments"""
        file = SimpleUploadedFile("test.pdf", b"content", content_type="application/pdf")
        payment = Payment.objects.create(
            amount=100,
            payment_recipient=self.recipient,
            professional=self.professional_user,
            proof_of_payment_file=file,
            operator_user=self.operator_user,
            patient_name="Test Patient",
        )

        self.client.login(username="professional", password="pass")

        # Try to access payment delete form
        resp = self.client.get(
            reverse("admin:payment_instructions_payment_delete", args=[payment.id])
        )
        self.assertEqual(resp.status_code, 403)  # Forbidden

    def test_admin_can_access_all_models(self):
        """Test that administrators can access all admin models"""
        self.client.login(username="admin", password="pass")

        # Should be able to access all admin pages
        urls = [
            reverse("admin:payment_instructions_user_changelist"),
            reverse("admin:payment_instructions_paymentrecipient_changelist"),
            reverse("admin:payment_instructions_payment_changelist"),
        ]

        for url in urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f"Admin should access {url}")

    def test_operator_can_access_payments_and_recipients(self):
        """Test that operators can access payments and recipients but not users"""
        self.client.login(username="operator", password="pass")

        # Should be able to access payments and recipients
        resp = self.client.get(reverse("admin:payment_instructions_payment_changelist"))
        self.assertEqual(resp.status_code, 200)

        # Should not be able to payments recipients
        resp = self.client.get(reverse("admin:payment_instructions_paymentrecipient_changelist"))
        self.assertEqual(resp.status_code, 403)

        # Should not be able to access users
        resp = self.client.get(reverse("admin:payment_instructions_user_changelist"))
        self.assertEqual(resp.status_code, 403)


class AdminModelPermissionTests(BaseTestCase):
    """Test admin model permission methods directly"""

    def setUp(self):
        self.admin_site = AdminSite()
        self.user_admin = UserAdmin(User, self.admin_site)
        self.payment_admin = PaymentAdmin(Payment, self.admin_site)
        self.recipient_admin = PaymentRecipientAdmin(PaymentRecipient, self.admin_site)

        # Create users
        self.admin_user = User.objects.create_user(
            username="admin", email="admin@example.com", role=User.ADMINISTRATOR, is_staff=True
        )

        self.professional_user = User.objects.create_user(
            username="professional",
            email="professional@example.com",
            role=User.PROFESSIONAL,
            is_staff=True,
        )

        self.operator_user = User.objects.create_user(
            username="operator", email="operator@example.com", role=User.OPERATOR, is_staff=True
        )

    def _create_request(self, user):
        """Helper to create a mock request with user"""
        request = HttpRequest()
        request.user = user
        return request

    def test_user_admin_permissions_for_professional(self):
        """Test UserAdmin permissions for professional users"""
        request = self._create_request(self.professional_user)

        # Professionals should not have module permission
        self.assertFalse(self.user_admin.has_module_permission(request))

        # Should not be able to add, change, or delete users
        self.assertFalse(self.user_admin.has_add_permission(request))
        self.assertFalse(self.user_admin.has_change_permission(request))
        self.assertFalse(self.user_admin.has_delete_permission(request))

    def test_payment_admin_permissions_for_professional(self):
        """Test PaymentAdmin permissions for professional users"""
        request = self._create_request(self.professional_user)

        # Professionals should not be able to add payments
        self.assertFalse(self.payment_admin.has_add_permission(request))

        # Should not be able to change or delete payments
        self.assertFalse(self.payment_admin.has_change_permission(request))
        self.assertFalse(self.payment_admin.has_delete_permission(request))

    def test_payment_recipient_admin_permissions_for_professional(self):
        """Test PaymentRecipientAdmin permissions for professional users"""
        request = self._create_request(self.professional_user)

        # Professionals should not have module permission
        self.assertFalse(self.recipient_admin.has_module_permission(request))

        # Should not be able to add, change, or delete recipients
        self.assertFalse(self.recipient_admin.has_add_permission(request))
        self.assertFalse(self.recipient_admin.has_change_permission(request))
        self.assertFalse(self.recipient_admin.has_delete_permission(request))

    def test_admin_user_has_all_permissions(self):
        """Test that admin users have all permissions"""
        request = self._create_request(self.admin_user)

        # Admin should have all permissions for all models
        for admin_class in [self.user_admin, self.payment_admin, self.recipient_admin]:
            self.assertTrue(admin_class.has_module_permission(request))
            self.assertTrue(admin_class.has_add_permission(request))
            self.assertTrue(admin_class.has_change_permission(request))
            self.assertTrue(admin_class.has_delete_permission(request))

    def test_payment_admin_queryset_filtering(self):
        """Test that PaymentAdmin filters queryset correctly for professionals"""
        # Create test data
        recipient = PaymentRecipient.objects.create(
            name="Test",
            alias="test",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )

        other_professional = User.objects.create_user(
            username="other_professional", email="other@example.com", role=User.PROFESSIONAL
        )

        file1 = SimpleUploadedFile("test1.pdf", b"content1", content_type="application/pdf")
        file2 = SimpleUploadedFile("test2.pdf", b"content2", content_type="application/pdf")

        # Create payments for different professionals
        payment1 = Payment.objects.create(
            amount=100,
            payment_recipient=recipient,
            professional=self.professional_user,
            proof_of_payment_file=file1,
            operator_user=self.operator_user,
            patient_name="Test Patient",
        )

        payment2 = Payment.objects.create(
            amount=200,
            payment_recipient=recipient,
            professional=other_professional,
            proof_of_payment_file=file2,
            operator_user=self.operator_user,
            patient_name="Other Patient",
        )

        # Test professional queryset filtering
        professional_request = self._create_request(self.professional_user)
        professional_qs = self.payment_admin.get_queryset(professional_request)

        # Professional should only see their own payments
        self.assertIn(payment1, professional_qs)
        self.assertNotIn(payment2, professional_qs)

        # Test admin queryset (should see all)
        admin_request = self._create_request(self.admin_user)
        admin_qs = self.payment_admin.get_queryset(admin_request)

        # Admin should see all payments
        self.assertIn(payment1, admin_qs)
        self.assertIn(payment2, admin_qs)

    def test_payment_admin_form_professional_filtering(self):
        """Test that PaymentAdmin form filters professionals correctly"""
        request = self._create_request(self.admin_user)

        # Create additional users
        inactive_professional = User.objects.create_user(
            username="inactive_professional",
            email="inactive@example.com",
            role=User.PROFESSIONAL,
            is_active=False,
        )

        non_professional = User.objects.create_user(
            username="non_professional", email="nonspec@example.com", role=User.OPERATOR
        )

        # Get form
        form = self.payment_admin.get_form(request)

        # Check professional field queryset
        if "professional" in form.base_fields:
            professional_qs = form.base_fields["professional"].queryset

            # Should include active professionals only
            self.assertIn(self.professional_user, professional_qs)
            self.assertNotIn(inactive_professional, professional_qs)
            self.assertNotIn(non_professional, professional_qs)


class PaymentAdminBypassValidationTests(BaseTestCase):
    """El administrador desde el admin de Django no tiene ninguna restricción de
    Payment.clean() al crear/editar: ni topes, ni nombre de paciente, ni rol de
    profesional, ni que el tipo de pago coincida con el destinatario."""

    def setUp(self):
        self.admin_site = AdminSite()
        self.payment_admin = PaymentAdmin(Payment, self.admin_site)

        self.admin_user = User.objects.create_user(
            username="admin", email="admin@example.com", role=User.ADMINISTRATOR, is_staff=True
        )
        self.operator_user = User.objects.create_user(
            username="operator", email="operator@example.com", role=User.OPERATOR, is_staff=True
        )
        self.professional = User.objects.create_user(
            username="professional1", email="pro@example.com", role=User.PROFESSIONAL
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Test",
            alias="test",
            cbu="1" * 22,
            max_amount=100,
            min_threshold=0,
            priority_order=1,
        )

    def _create_request(self, user):
        request = HttpRequest()
        request.user = user
        return request

    def _form_data(self, **overrides):
        data = {
            "payment_type": Payment.REGULAR,
            "metodo": Payment.TRANSFERENCIA,
            "estado": Payment.COMPLETADO,
            "amount": 100000,  # muy por encima del tope de 100
            "patient_name": "Paciente de prueba",
            "payment_recipient": self.recipient.id,
            "professional": self.professional.id,
            "operator_user": self.admin_user.id,
            "created_at_0": "2026-01-01",
            "created_at_1": "10:00:00",
            "reviewed": "",
        }
        data.update(overrides)
        return data

    def test_administrador_puede_superar_el_tope(self):
        request = self._create_request(self.admin_user)
        form_class = self.payment_admin.get_form(request)
        form = form_class(data=self._form_data())

        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.instance._admin_bypass_validation)

    def test_administrador_no_puede_asignar_pago_regular_a_destinatario_de_senas(self):
        """La coherencia payment_type vs recipient_type es estructural, no un tope que
        el administrador quiera saltear: rompe reportes si se permite. El bypass ya
        no la saltea, ni siquiera para el administrador."""
        deposit_recipient = PaymentRecipient.objects.create(
            name="Solo señas",
            alias="senas",
            cbu="2" * 22,
            recipient_type=PaymentRecipient.DEPOSIT_ONLY,
            max_amount=1,
            priority_order=2,
        )
        request = self._create_request(self.admin_user)
        form_class = self.payment_admin.get_form(request)
        form = form_class(
            data=self._form_data(
                payment_type=Payment.REGULAR, payment_recipient=deposit_recipient.id
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("payment_recipient", form.errors)

    def test_save_model_guarda_el_pago_del_administrador_por_encima_del_tope(self):
        """El administrador carga a mano lo que ya pasó en la vida real: el tope no lo
        bloquea. Antes esto además marcaba excede_politica; ese campo se fue con las
        reservas de cupo, pero el bypass sigue igual."""
        request = self._create_request(self.admin_user)
        form_class = self.payment_admin.get_form(request)
        form = form_class(data=self._form_data())
        self.assertTrue(form.is_valid(), form.errors)

        self.payment_admin.save_model(request, form.instance, form, change=False)

        payment = Payment.objects.get(pk=form.instance.pk)
        self.assertEqual(payment.amount, 100000)

    def test_no_administrador_no_activa_el_bypass(self):
        """El mecanismo en sí no distingue por permiso de la vista, solo por rol: lo
        prueba directo para no depender de has_add_permission (cubierto aparte)."""
        request = self._create_request(self.operator_user)
        form_class = self.payment_admin.get_form(request)
        form = form_class(data=self._form_data())

        self.assertFalse(form.is_valid())
        self.assertFalse(form.instance._admin_bypass_validation)


class ProfessionalRoleValidationTests(BaseTestCase):
    """Test professional role validation and functionality"""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1", email="spec@example.com", role=User.PROFESSIONAL
        )

        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", role=User.OPERATOR
        )

        self.recipient = PaymentRecipient.objects.create(
            name="Test",
            alias="test",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )

    def test_professional_role_constants(self):
        """Test that professional role constants are defined correctly"""
        self.assertEqual(User.PROFESSIONAL, "professional")
        self.assertIn((User.PROFESSIONAL, "Professional"), User.ROLE_CHOICES)

    def test_is_professional_method(self):
        """Test is_professional method works correctly"""
        self.assertTrue(self.professional.is_professional())
        self.assertFalse(self.operator.is_professional())

    def test_get_current_month_amount_for_professional(self):
        """Test get_current_month_amount method for professionals"""
        # Initially should be 0
        self.assertEqual(self.professional.get_current_month_amount(), 0)

        # Create a payment
        file = SimpleUploadedFile("test.pdf", b"content", content_type="application/pdf")
        Payment.objects.create(
            amount=150,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Test Patient",
        )

        # Should now return the payment amount
        self.assertEqual(self.professional.get_current_month_amount(), 150)

    def test_get_current_month_amount_for_non_professional(self):
        """Test get_current_month_amount method for non-professionals"""
        # Should always return 0 for non-professionals
        self.assertEqual(self.operator.get_current_month_amount(), 0)

    def test_payment_professional_field_limit_choices(self):
        """Test that Payment.professional field limits choices to professionals"""
        field = Payment._meta.get_field("professional")
        limit_choices = field.get_limit_choices_to()

        self.assertEqual(limit_choices, {"role": User.PROFESSIONAL})

    def test_payment_professional_related_name(self):
        """Test that Payment.professional has correct related_name"""
        # Check that professional users have professional_payments attribute
        self.assertTrue(hasattr(self.professional, "professional_payments"))

        # Create a payment and verify the relationship
        file = SimpleUploadedFile("test.pdf", b"content", content_type="application/pdf")
        payment = Payment.objects.create(
            amount=100,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Test Patient",
        )

        # Check that the payment appears in professional_payments
        self.assertIn(payment, self.professional.professional_payments.all())


class PaymentReviewedAdminTests(BaseTestCase):
    """Revisado + permiso mark_payment_reviewed en admin de pagos"""

    def setUp(self):
        self.client = Client()
        self.recipient = PaymentRecipient.objects.create(
            name="R1",
            alias="r1",
            cbu="1" * 22,
            max_amount=1_000_000,
            min_threshold=0,
            priority_order=1,
        )
        self.operator = User.objects.create_user(
            username="oprev",
            email="oprev@example.com",
            password="pass",
            role=User.OPERATOR,
            is_staff=True,
        )
        self.administrator = User.objects.create_user(
            username="admrev",
            email="admrev@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
        )
        self.professional = User.objects.create_user(
            username="sprev",
            email="sprev@example.com",
            password="pass",
            role=User.PROFESSIONAL,
            is_staff=True,
        )
        self._mark_perm = Permission.objects.get(
            codename="mark_payment_reviewed",
            content_type=ContentType.objects.get_for_model(Payment),
        )

    def _payment_file(self):
        return SimpleUploadedFile("x.pdf", b"x", content_type="application/pdf")

    def test_save_model_mark_reviewed_only_updates_flag(self):
        self.operator.user_permissions.add(self._mark_perm)

        file = self._payment_file()
        payment = Payment.objects.create(
            amount=500,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Test Patient",
            reviewed=False,
        )

        class Form:
            cleaned_data = {"reviewed": True}

        request = HttpRequest()
        request.user = self.operator
        admin = PaymentAdmin(Payment, site)
        obj = Payment.objects.get(pk=payment.pk)
        obj.amount = 999_999
        admin.save_model(request, obj, Form(), change=True)

        payment.refresh_from_db()
        self.assertTrue(payment.reviewed)
        self.assertEqual(payment.amount, 500)

    def test_changelist_shows_muted_color_when_reviewed(self):
        file = self._payment_file()
        p = Payment.objects.create(
            amount=400,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Paciente Único",
            reviewed=True,
        )
        self.client.force_login(self.administrator)
        resp = self.client.get(reverse("admin:payment_instructions_payment_changelist"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn("#a4a4a4", html)
        self.assertIn("Paciente Único", html)

    def test_reviewed_filter_only_reviewed(self):
        file = self._payment_file()
        Payment.objects.create(
            amount=100,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Paciente_Unseen_Xy9",
            reviewed=False,
        )
        Payment.objects.create(
            amount=200,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Paciente_Seen_Ab7",
            reviewed=True,
        )
        self.client.force_login(self.administrator)
        resp = self.client.get(
            reverse("admin:payment_instructions_payment_changelist"),
            {"reviewed": "1"},
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn("Paciente_Seen_Ab7", html)
        self.assertNotIn("Paciente_Unseen_Xy9", html)

    def test_changelist_shows_reviewed_checkbox_for_administrator(self):
        file = self._payment_file()
        payment = Payment.objects.create(
            amount=300,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Paciente_Checkbox_Admin",
            reviewed=False,
        )
        self.client.force_login(self.administrator)
        resp = self.client.get(reverse("admin:payment_instructions_payment_changelist"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn("payment-reviewed-toggle", html)
        self.assertIn(f'data-payment-id="{payment.pk}"', html)

    def test_changelist_reviewed_readonly_for_professional_without_perm(self):
        file = self._payment_file()
        Payment.objects.create(
            amount=300,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Paciente_Spec_Readonly",
            reviewed=True,
        )
        self.client.force_login(self.professional)
        resp = self.client.get(reverse("admin:payment_instructions_payment_changelist"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertNotIn('type="checkbox" class="payment-reviewed-toggle"', html)
        self.assertIn('data-payment-reviewed="1"', html)
        self.assertIn("Paciente_Spec_Readonly", html)

    def test_toggle_reviewed_ajax_updates_flag(self):
        self.operator.user_permissions.add(self._mark_perm)
        file = self._payment_file()
        payment = Payment.objects.create(
            amount=500,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Paciente_Toggle_Ajax",
            reviewed=False,
        )
        self.client.force_login(self.operator)
        url = reverse("admin:payment_instructions_payment_toggle_reviewed")
        resp = self.client.post(
            url,
            {"payment_id": payment.pk, "reviewed": "true"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertTrue(resp.json()["reviewed"])
        payment.refresh_from_db()
        self.assertTrue(payment.reviewed)

    def test_toggle_reviewed_ajax_forbidden_without_perm(self):
        file = self._payment_file()
        payment = Payment.objects.create(
            amount=500,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
            patient_name="Paciente_Toggle_Forbidden",
            reviewed=False,
        )
        self.client.force_login(self.professional)
        url = reverse("admin:payment_instructions_payment_toggle_reviewed")
        resp = self.client.post(
            url,
            {"payment_id": payment.pk, "reviewed": "true"},
        )
        self.assertEqual(resp.status_code, 403)
        payment.refresh_from_db()
        self.assertFalse(payment.reviewed)

    def test_toggle_reviewed_ajax_cannot_access_other_operator_payment(self):
        self.operator.user_permissions.add(self._mark_perm)
        other_operator = User.objects.create_user(
            username="opother",
            email="opother@example.com",
            password="pass",
            role=User.OPERATOR,
            is_staff=True,
        )
        file = self._payment_file()
        payment = Payment.objects.create(
            amount=500,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=other_operator,
            patient_name="Paciente_Other_Op",
            reviewed=False,
        )
        self.client.force_login(self.operator)
        url = reverse("admin:payment_instructions_payment_toggle_reviewed")
        resp = self.client.post(
            url,
            {"payment_id": payment.pk, "reviewed": "true"},
        )
        self.assertEqual(resp.status_code, 404)
        payment.refresh_from_db()
        self.assertFalse(payment.reviewed)


class OperatorPaymentHistoryLimitTests(BaseTestCase):
    """Operarios solo ven pagos de los últimos OPERATOR_PAYMENT_HISTORY_DAYS días."""

    def setUp(self):
        self.client = Client()
        self.payment_admin = PaymentAdmin(Payment, site)
        self.recipient = PaymentRecipient.objects.create(
            name="R Hist",
            alias="r_hist",
            cbu="1" * 22,
            max_amount=1_000_000,
            min_threshold=0,
            priority_order=1,
        )
        self.deposit_recipient = PaymentRecipient.objects.create(
            name="Señas Hist",
            alias="senas_hist",
            cbu="2" * 22,
            recipient_type=PaymentRecipient.DEPOSIT_ONLY,
            is_active=True,
        )
        self.operator = User.objects.create_user(
            username="op_hist",
            email="op_hist@example.com",
            password="pass",
            role=User.OPERATOR,
            is_staff=True,
        )
        self.other_operator = User.objects.create_user(
            username="op_hist_other",
            email="op_hist_other@example.com",
            password="pass",
            role=User.OPERATOR,
            is_staff=True,
        )
        self.administrator = User.objects.create_user(
            username="adm_hist",
            email="adm_hist@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
        )
        self.professional = User.objects.create_user(
            username="sp_hist",
            email="sp_hist@example.com",
            password="pass",
            role=User.PROFESSIONAL,
            is_staff=True,
        )
        self._mark_perm = Permission.objects.get(
            codename="mark_payment_reviewed",
            content_type=ContentType.objects.get_for_model(Payment),
        )
        self._deposit_view_perm = Permission.objects.get(
            codename="view_all_deposit_payments_in_admin",
            content_type=ContentType.objects.get_for_model(Payment),
        )

    def _payment_file(self):
        return SimpleUploadedFile("hist.pdf", b"x", content_type="application/pdf")

    def _create_payment(
        self,
        *,
        days_ago,
        operator_user,
        patient_name,
        payment_type=Payment.REGULAR,
        payment_recipient=None,
    ):
        created_at = timezone.now() - timedelta(days=days_ago)
        payment = Payment.objects.create(
            amount=100,
            payment_recipient=payment_recipient or self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._payment_file(),
            operator_user=operator_user,
            patient_name=patient_name,
            payment_type=payment_type,
        )
        Payment.objects.filter(pk=payment.pk).update(created_at=created_at)
        payment.refresh_from_db()
        return payment

    def _operator_request(self):
        request = HttpRequest()
        request.user = self.operator
        return request

    def test_operator_queryset_excludes_payments_older_than_history_window(self):
        recent = self._create_payment(
            days_ago=10,
            operator_user=self.operator,
            patient_name="Paciente_Reciente_Hist",
        )
        old = self._create_payment(
            days_ago=OPERATOR_PAYMENT_HISTORY_DAYS + 1,
            operator_user=self.operator,
            patient_name="Paciente_Antiguo_Hist",
        )

        qs = self.payment_admin.get_queryset(self._operator_request())
        ids = set(qs.values_list("id", flat=True))

        self.assertIn(recent.id, ids)
        self.assertNotIn(old.id, ids)

    def test_operator_changelist_hides_old_payments(self):
        self._create_payment(
            days_ago=10,
            operator_user=self.operator,
            patient_name="Paciente_Visible_Hist",
        )
        self._create_payment(
            days_ago=OPERATOR_PAYMENT_HISTORY_DAYS + 1,
            operator_user=self.operator,
            patient_name="Paciente_Oculto_Hist",
        )

        self.client.force_login(self.operator)
        resp = self.client.get(reverse("admin:payment_instructions_payment_changelist"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn("Paciente_Visible_Hist", html)
        self.assertNotIn("Paciente_Oculto_Hist", html)

    def test_operator_with_deposit_perm_sees_recent_foreign_deposits_only(self):
        self.operator.user_permissions.add(self._deposit_view_perm)
        recent_deposit = self._create_payment(
            days_ago=5,
            operator_user=self.other_operator,
            patient_name="Senia_Reciente_Ajena",
            payment_type=Payment.DEPOSIT,
            payment_recipient=self.deposit_recipient,
        )
        old_deposit = self._create_payment(
            days_ago=OPERATOR_PAYMENT_HISTORY_DAYS + 1,
            operator_user=self.other_operator,
            patient_name="Senia_Antigua_Ajena",
            payment_type=Payment.DEPOSIT,
            payment_recipient=self.deposit_recipient,
        )

        qs = self.payment_admin.get_queryset(self._operator_request())
        ids = set(qs.values_list("id", flat=True))

        self.assertIn(recent_deposit.id, ids)
        self.assertNotIn(old_deposit.id, ids)

    def test_toggle_reviewed_returns_404_for_old_own_payment(self):
        self.operator.user_permissions.add(self._mark_perm)
        old_payment = self._create_payment(
            days_ago=OPERATOR_PAYMENT_HISTORY_DAYS + 1,
            operator_user=self.operator,
            patient_name="Paciente_Toggle_Viejo",
        )

        self.client.force_login(self.operator)
        url = reverse("admin:payment_instructions_payment_toggle_reviewed")
        resp = self.client.post(
            url,
            {"payment_id": old_payment.pk, "reviewed": "true"},
        )
        self.assertEqual(resp.status_code, 404)
        old_payment.refresh_from_db()
        self.assertFalse(old_payment.reviewed)

    def test_administrator_still_sees_old_payments(self):
        old = self._create_payment(
            days_ago=OPERATOR_PAYMENT_HISTORY_DAYS + 1,
            operator_user=self.operator,
            patient_name="Paciente_Admin_Viejo",
        )

        request = HttpRequest()
        request.user = self.administrator
        qs = self.payment_admin.get_queryset(request)

        self.assertIn(old, qs)

    def test_operator_changelist_hides_date_hierarchy(self):
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("admin:payment_instructions_payment_changelist"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertNotIn("created_at__year", html)


class PaymentAdminEstadoFilterTests(BaseTestCase):
    """El listado de pagos arranca mostrando solo los completados.

    Se usa para auditar plata que entró: los pendientes son trabajo en curso y los
    cancelados son ruido para esa pregunta. Se ven eligiendo el estado en el filtro."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="super_estado", email="super_estado@example.com", password="pass"
        )
        self.professional = User.objects.create_user(
            username="prof_estado",
            email="prof_estado@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Cuenta filtro",
            alias="cuenta.filtro",
            cbu="7" * 22,
            max_amount=1_000_000,
            priority_order=1,
        )
        self.completado = self._crear_pago("Paciente Completado", Payment.COMPLETADO)
        self.pendiente = self._crear_pago("Paciente Pendiente", Payment.PENDIENTE)
        self.cancelado = self._crear_pago(
            "Paciente Cancelado",
            Payment.CANCELADO,
            motivo_cancelacion=Payment.ERROR_DE_CARGA,
        )
        self.client.login(username="super_estado", password="pass")

    def _crear_pago(self, patient_name, estado, **extra):
        return Payment.objects.create(
            amount=1000,
            payment_type=Payment.REGULAR,
            metodo=Payment.TRANSFERENCIA,
            estado=estado,
            payment_recipient=self.recipient,
            patient_name=patient_name,
            professional=self.professional,
            operator_user=self.admin_user,
            **extra,
        )

    def _changelist(self, **params):
        return self.client.get(reverse("admin:payment_instructions_payment_changelist"), params)

    def test_sin_filtro_solo_se_ven_los_completados(self):
        resp = self._changelist()
        self.assertContains(resp, "Paciente Completado")
        self.assertNotContains(resp, "Paciente Pendiente")
        self.assertNotContains(resp, "Paciente Cancelado")

    def test_con_todos_se_ven_los_tres(self):
        resp = self._changelist(estado="todos")
        self.assertContains(resp, "Paciente Completado")
        self.assertContains(resp, "Paciente Pendiente")
        self.assertContains(resp, "Paciente Cancelado")

    def test_filtrando_por_pendiente_se_ven_solo_los_pendientes(self):
        resp = self._changelist(estado=Payment.PENDIENTE)
        self.assertContains(resp, "Paciente Pendiente")
        self.assertNotContains(resp, "Paciente Completado")

    def test_filtrando_por_cancelado_se_ven_solo_los_cancelados(self):
        resp = self._changelist(estado=Payment.CANCELADO)
        self.assertContains(resp, "Paciente Cancelado")
        self.assertNotContains(resp, "Paciente Completado")

    def test_la_columna_estado_no_se_muestra(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        columnas = list(PaymentAdmin(Payment, site).get_list_display(request))
        self.assertNotIn("estado_display", columnas)
        self.assertNotIn("estado", columnas)


class _MensajesEnMemoria(BaseStorage):
    """Backend de mensajes que no toca la sesión, para probar acciones del admin."""

    def _get(self, *args, **kwargs):
        return [], True

    def _store(self, messages, response, *args, **kwargs):
        return []


class CancelarPagosAccionTests(BaseTestCase):
    """La acción masiva solo toca pendientes.

    Un COMPLETADO es plata que ya se movió: anularlo de a montones sin confirmación
    saca los importes de los totales del mes y del cupo consumido sin que nadie lo
    haya decidido pago por pago."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="super_cancel", email="super_cancel@example.com", password="pass"
        )
        self.professional = User.objects.create_user(
            username="prof_cancel",
            email="prof_cancel@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Cuenta accion",
            alias="cuenta.accion",
            cbu="8" * 22,
            max_amount=1_000_000,
            priority_order=1,
        )
        self.pendiente = Payment.objects.crear_pendiente(
            payment_recipient=self.recipient,
            amount=10_000,
            operator_user=self.admin_user,
            patient_name="Paciente Pendiente",
            professional=self.professional,
        )
        self.completado = Payment.objects.create(
            amount=20_000,
            payment_type=Payment.REGULAR,
            metodo=Payment.TRANSFERENCIA,
            estado=Payment.COMPLETADO,
            payment_recipient=self.recipient,
            patient_name="Paciente Completado",
            professional=self.professional,
            operator_user=self.admin_user,
        )

    def _correr_accion(self, queryset):
        request = RequestFactory().post("/")
        request.user = self.admin_user
        # message_user necesita un backend de mensajes; el de sesión pediría middleware.
        request._messages = _MensajesEnMemoria(request)
        PaymentAdmin(Payment, site).cancelar_pagos(request, queryset)

    def test_cancela_los_pendientes(self):
        self._correr_accion(Payment.objects.all())
        self.pendiente.refresh_from_db()
        self.assertEqual(self.pendiente.estado, Payment.CANCELADO)
        self.assertEqual(self.pendiente.motivo_cancelacion, Payment.ERROR_DE_CARGA)

    def test_no_toca_los_completados(self):
        self._correr_accion(Payment.objects.all())
        self.completado.refresh_from_db()
        self.assertEqual(self.completado.estado, Payment.COMPLETADO)

    def test_el_cupo_del_completado_sigue_consumido(self):
        self._correr_accion(Payment.objects.all())
        self.assertEqual(self.recipient.get_current_month_received(), 20_000)


class AdminBypassCancelacionTests(BaseTestCase):
    """El bypass del administrador saltea las reglas de negocio, pero no deja guardar
    una cancelación sin motivo: eso no es una regla que se quiera saltear, es un dato
    que quedaría roto y no se puede reconstruir después."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="super_bypass", email="super_bypass@example.com", password="pass"
        )
        self.professional = User.objects.create_user(
            username="prof_bypass",
            email="prof_bypass@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Cuenta bypass",
            alias="cuenta.bypass",
            cbu="6" * 22,
            max_amount=1000,
            priority_order=1,
        )

    def _payment(self, **extra):
        payment = Payment(
            amount=999_999,  # muy por encima del tope, a propósito
            payment_type=Payment.REGULAR,
            metodo=Payment.TRANSFERENCIA,
            payment_recipient=self.recipient,
            patient_name="Paciente Ficticio",
            professional=self.professional,
            operator_user=self.admin_user,
            **extra,
        )
        payment._admin_bypass_validation = True
        return payment

    def test_el_bypass_sigue_permitiendo_superar_el_tope(self):
        self._payment(estado=Payment.COMPLETADO).clean()  # no levanta

    def test_el_bypass_no_permite_cancelar_sin_motivo(self):
        with self.assertRaises(ValidationError) as ctx:
            self._payment(estado=Payment.CANCELADO).clean()
        self.assertIn("motivo_cancelacion", ctx.exception.message_dict)

    def test_el_bypass_no_permite_motivo_otro_sin_detalle(self):
        payment = self._payment(estado=Payment.CANCELADO, motivo_cancelacion=Payment.OTRO)
        with self.assertRaises(ValidationError) as ctx:
            payment.clean()
        self.assertIn("observacion_cancelacion", ctx.exception.message_dict)

    def test_el_bypass_acepta_una_cancelacion_completa(self):
        self._payment(
            estado=Payment.CANCELADO, motivo_cancelacion=Payment.PACIENTE_NO_PAGO
        ).clean()  # no levanta


class AdminBypassCoherenciaTests(BaseTestCase):
    """El bypass del administrador sigue permitiendo superar el tope, pero no las
    coherencias estructurales: esas rompen reportes y no son una corrección de datos,
    son un dato inválido."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="super_coherencia", email="super_coherencia@example.com", password="pass"
        )
        self.professional = User.objects.create_user(
            username="prof_coherencia",
            email="prof_coherencia@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="op_coherencia",
            email="op_coherencia@example.com",
            password="pass",
            role=User.OPERATOR,
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Cuenta coherencia",
            alias="cuenta.coherencia",
            cbu="7" * 22,
            max_amount=1000,
            priority_order=1,
        )
        self.deposit_recipient = PaymentRecipient.objects.create(
            name="Cuenta señas coherencia",
            alias="cuenta.senas.coherencia",
            recipient_type=PaymentRecipient.DEPOSIT_ONLY,
            priority_order=2,
        )

    def _payment(self, **extra):
        base = dict(
            amount=999_999,  # muy por encima del tope, a propósito
            payment_type=Payment.REGULAR,
            metodo=Payment.TRANSFERENCIA,
            payment_recipient=self.recipient,
            patient_name="Paciente Ficticio",
            professional=self.professional,
            operator_user=self.admin_user,
        )
        base.update(extra)
        payment = Payment(**base)
        payment._admin_bypass_validation = True
        return payment

    def test_el_bypass_sigue_permitiendo_superar_el_tope(self):
        self._payment().clean()  # no levanta

    def test_el_bypass_no_permite_nombre_de_paciente_vacio(self):
        with self.assertRaises(ValidationError) as ctx:
            self._payment(patient_name="").clean()
        self.assertIn("patient_name", ctx.exception.message_dict)

    def test_el_bypass_no_permite_profesional_sin_rol(self):
        with self.assertRaises(ValidationError) as ctx:
            self._payment(professional=self.operator).clean()
        self.assertIn("professional", ctx.exception.message_dict)

    def test_el_bypass_no_permite_metodo_sin_destinatario_coherente(self):
        with self.assertRaises(ValidationError) as ctx:
            self._payment(metodo=Payment.EFECTIVO, payment_recipient=self.recipient).clean()
        self.assertIn("payment_recipient", ctx.exception.message_dict)

    def test_el_bypass_no_permite_tipo_de_pago_cruzado_con_destinatario(self):
        with self.assertRaises(ValidationError) as ctx:
            self._payment(payment_type=Payment.DEPOSIT, payment_recipient=self.recipient).clean()
        self.assertIn("payment_recipient", ctx.exception.message_dict)
