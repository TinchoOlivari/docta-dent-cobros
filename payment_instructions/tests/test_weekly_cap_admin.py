from django.test import Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.utils import timezone

from payment_instructions.models import PaymentRecipient, Payment
from payment_instructions.admin import PaymentRecipientAdmin
from payment_instructions.tests import BaseTestCase

User = get_user_model()


class WeeklyCapAdminDisplayTests(BaseTestCase):
    """Test admin display methods for weekly cap functionality"""

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
            is_superuser=True,
        )

        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )

        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )

        self.admin_site = AdminSite()
        self.recipient_admin = PaymentRecipientAdmin(PaymentRecipient, self.admin_site)

    def _create_recipient_with_weekly_cap(self, weekly_cap=500, max_amount=1000, alias_suffix=None):
        """Helper to create recipient with weekly cap"""
        if alias_suffix is None:
            alias_suffix = PaymentRecipient.objects.count()
        return PaymentRecipient.objects.create(
            name="Test Recipient",
            alias=f"test_recipient_{alias_suffix}",
            max_amount=max_amount,
            weekly_cap_amount=weekly_cap,
            min_threshold=50,
            priority_order=1,
        )

    def _create_payment(self, recipient, amount, created_at=None):
        """Helper to create payment"""
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        payment = Payment.objects.create(
            amount=amount,
            payment_recipient=recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )
        if created_at:
            payment.created_at = created_at
            payment.save(update_fields=["created_at"])
        return payment

    def test_weekly_cap_display_method(self):
        """Test weekly_cap_display admin method"""
        # Test with weekly cap set
        recipient_with_cap = self._create_recipient_with_weekly_cap(weekly_cap=400)
        display = self.recipient_admin.weekly_cap_display(recipient_with_cap)
        self.assertEqual(display, "$400")

        # Test with no weekly cap (0)
        recipient_no_cap = self._create_recipient_with_weekly_cap(weekly_cap=0)
        display = self.recipient_admin.weekly_cap_display(recipient_no_cap)
        self.assertEqual(display, "Sin límite")

        # Test with None (should default to 0)
        recipient_none_cap = PaymentRecipient.objects.create(
            name="None Cap",
            alias="none_cap",
            max_amount=1000,
            min_threshold=0,
            priority_order=2,
        )
        display = self.recipient_admin.weekly_cap_display(recipient_none_cap)
        self.assertEqual(display, "Sin límite")

    def test_current_week_received_display_method(self):
        """Test current_week_received admin method"""
        recipient = self._create_recipient_with_weekly_cap()

        # Test with no payments
        display = self.recipient_admin.current_week_received(recipient)
        self.assertEqual(display, "$0")

        # Test with payments this week
        self._create_payment(recipient, 150)
        self._create_payment(recipient, 200)

        display = self.recipient_admin.current_week_received(recipient)
        self.assertEqual(display, "$350")

    def test_current_week_remaining_display_method(self):
        """Test current_week_remaining admin method"""
        recipient = self._create_recipient_with_weekly_cap(weekly_cap=500)

        # Test with no payments
        display = self.recipient_admin.current_week_remaining(recipient)
        self.assertEqual(display, "$500")

        # Test with payments this week
        self._create_payment(recipient, 200)

        display = self.recipient_admin.current_week_remaining(recipient)
        self.assertEqual(display, "$300")

        # Test when weekly cap is reached
        self._create_payment(recipient, 300)  # Total: 500

        display = self.recipient_admin.current_week_remaining(recipient)
        self.assertEqual(display, "$0")

    def test_current_week_remaining_no_weekly_cap(self):
        """Test current_week_remaining display method when no weekly cap is set"""
        recipient = self._create_recipient_with_weekly_cap(weekly_cap=0)  # No weekly cap

        display = self.recipient_admin.current_week_remaining(recipient)
        self.assertEqual(display, "Sin límite")

    def test_current_week_remaining_considers_monthly_limit(self):
        """Test that current_week_remaining considers both weekly and monthly limits"""
        recipient = self._create_recipient_with_weekly_cap(weekly_cap=500, max_amount=600)

        # Create payments that approach monthly limit
        self._create_payment(recipient, 400)  # Monthly remaining: 200, Weekly remaining: 100

        display = self.recipient_admin.current_week_remaining(recipient)
        # Should show the lower of the two (monthly remaining: 200, weekly remaining: 100)
        self.assertEqual(display, "$100")

        # Create more payments to make monthly limit more restrictive
        self._create_payment(recipient, 50)  # Monthly remaining: 150, Weekly remaining: 50

        display = self.recipient_admin.current_week_remaining(recipient)
        # Should show weekly remaining (50) since it's lower
        self.assertEqual(display, "$50")

        # Create payment to make monthly limit even more restrictive
        self._create_payment(recipient, 30)  # Monthly remaining: 120, Weekly remaining: 20

        display = self.recipient_admin.current_week_remaining(recipient)
        # Should show weekly remaining (20) since it's still lower
        self.assertEqual(display, "$20")

    def test_received_per_week_of_month_display_method(self):
        """Test received_per_week_of_month admin method"""
        recipient = self._create_recipient_with_weekly_cap()

        # Test with no payments
        display = self.recipient_admin.received_per_week_of_month(recipient)
        lines = display.split("\n")
        # Should have entries for each week, all showing $0
        self.assertTrue(all("$0" in line for line in lines))

        # Create payments in different weeks of the month
        now = timezone.now()

        # Payment in first week of month
        first_week = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        self._create_payment(recipient, 100, created_at=first_week)

        # Payment in second week of month (if exists)
        if now.day > 7:
            second_week = now.replace(day=8, hour=12, minute=0, second=0, microsecond=0)
            self._create_payment(recipient, 150, created_at=second_week)

        display = self.recipient_admin.received_per_week_of_month(recipient)
        lines = display.split("\n")

        # Should show payments for respective weeks
        self.assertIn("$100", lines[0])  # First week
        if now.day > 7:
            self.assertIn("$150", lines[1])  # Second week


class WeeklyCapAdminFormTests(BaseTestCase):
    """Test admin form behavior with weekly cap fields"""

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
            is_superuser=True,
        )

        self.client = Client()
        self.client.login(username="admin", password="pass")

    def test_weekly_cap_field_in_admin_form(self):
        """Test that weekly_cap_amount field appears in admin form"""
        response = self.client.get(reverse("admin:payment_instructions_paymentrecipient_add"))
        self.assertEqual(response.status_code, 200)

        # Check that weekly_cap_amount field is present
        self.assertContains(response, "weekly_cap_amount")
        self.assertContains(response, "Monto máximo semanal")

    def test_weekly_cap_field_styling(self):
        """Test that weekly_cap_amount field has proper styling"""
        admin_site = AdminSite()
        recipient_admin = PaymentRecipientAdmin(PaymentRecipient, admin_site)

        # Mock request
        request = HttpRequest()
        request.user = self.admin_user

        form = recipient_admin.get_form(request)

        # Check that weekly_cap_amount field has width styling
        if "weekly_cap_amount" in form.base_fields:
            widget_attrs = form.base_fields["weekly_cap_amount"].widget.attrs
            self.assertIn("style", widget_attrs)
            self.assertIn("width: 20em", widget_attrs["style"])

    def test_weekly_cap_field_in_fieldsets(self):
        """Test that weekly_cap_amount field is in the correct fieldset"""
        admin_site = AdminSite()
        recipient_admin = PaymentRecipientAdmin(PaymentRecipient, admin_site)

        fieldsets = recipient_admin.fieldsets

        # Find the "Configuración de pago" fieldset
        config_fieldset = None
        for name, options in fieldsets:
            if name == "Configuración de pago":
                config_fieldset = options
                break

        self.assertIsNotNone(config_fieldset)
        self.assertIn("weekly_cap_amount", config_fieldset["fields"])

    def test_weekly_cap_readonly_fields(self):
        """Test that weekly cap related readonly fields are configured"""
        admin_site = AdminSite()
        recipient_admin = PaymentRecipientAdmin(PaymentRecipient, admin_site)

        readonly_fields = recipient_admin.readonly_fields

        # Check that weekly cap related fields are readonly
        self.assertIn("current_week_received", readonly_fields)
        self.assertIn("current_week_remaining", readonly_fields)


class WeeklyCapAdminValidationTests(BaseTestCase):
    """Test admin form validation for weekly cap constraints"""

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
            is_superuser=True,
        )

        self.client = Client()
        self.client.login(username="admin", password="pass")

    def test_admin_form_validates_weekly_cap_vs_max_amount(self):
        """Test that admin form validates weekly_cap_amount against max_amount"""
        # Try to create recipient with weekly cap exceeding max amount
        form_data = {
            "name": "Test Recipient",
            "alias": "test_recipient_1",
            "cbu": "1" * 22,
            "recipient_type": "REGULAR",
            "max_amount": 500,
            "weekly_cap_amount": 600,  # Exceeds max_amount
            "min_threshold": 0,
            "priority_order": 1,
            "is_active": True,
        }

        response = self.client.post(
            reverse("admin:payment_instructions_paymentrecipient_add"), form_data
        )

        # Should show form with validation errors
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no puede superar el monto máximo")

    def test_admin_form_validates_weekly_cap_vs_min_threshold(self):
        """Test that admin form validates weekly_cap_amount against min_threshold"""
        # Try to create recipient with weekly cap below min threshold
        form_data = {
            "name": "Test Recipient",
            "alias": "test_recipient_3",
            "cbu": "1" * 22,
            "recipient_type": "REGULAR",
            "max_amount": 1000,
            "weekly_cap_amount": 50,  # Below min_threshold
            "min_threshold": 100,
            "priority_order": 1,
            "is_active": True,
        }

        response = self.client.post(
            reverse("admin:payment_instructions_paymentrecipient_add"), form_data
        )

        # Should show form with validation errors
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no puede ser menor al monto mínimo")

    def test_admin_form_accepts_valid_weekly_cap(self):
        """Test that admin form accepts valid weekly cap values"""
        form_data = {
            "name": "Valid Recipient",
            "alias": "valid_recipient",
            "cbu": "1" * 22,
            "recipient_type": "REGULAR",
            "max_amount": 1000,
            "weekly_cap_amount": 500,  # Valid: between min_threshold and max_amount
            "min_threshold": 100,
            "priority_order": 1,
            "is_active": True,
        }

        response = self.client.post(
            reverse("admin:payment_instructions_paymentrecipient_add"), form_data, follow=True
        )

        # Should redirect to changelist (success)
        self.assertEqual(response.status_code, 200)

        # Verify recipient was created with correct weekly cap
        recipient = PaymentRecipient.objects.get(alias="valid_recipient")
        self.assertEqual(recipient.weekly_cap_amount, 500)

    def test_admin_form_accepts_zero_weekly_cap(self):
        """Test that admin form accepts zero weekly cap (no limit)"""
        form_data = {
            "name": "No Cap Recipient",
            "alias": "no_cap_recipient",
            "cbu": "2" * 22,
            "recipient_type": "REGULAR",
            "max_amount": 1000,
            "weekly_cap_amount": 0,  # No weekly cap
            "min_threshold": 100,
            "priority_order": 1,
            "is_active": True,
        }

        response = self.client.post(
            reverse("admin:payment_instructions_paymentrecipient_add"), form_data, follow=True
        )

        # Should redirect to changelist (success)
        self.assertEqual(response.status_code, 200)

        # Verify recipient was created with zero weekly cap
        recipient = PaymentRecipient.objects.get(alias="no_cap_recipient")
        self.assertEqual(recipient.weekly_cap_amount, 0)


class WeeklyCapAdminListDisplayTests(BaseTestCase):
    """Test admin list display with weekly cap information"""

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
            is_superuser=True,
        )

        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )

        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )

        self.client = Client()
        self.client.login(username="admin", password="pass")

        # Create test recipients
        self.recipient_with_cap = PaymentRecipient.objects.create(
            name="With Cap",
            alias="with_cap",
            max_amount=1000,
            weekly_cap_amount=400,
            min_threshold=50,
            priority_order=1,
        )

        self.recipient_no_cap = PaymentRecipient.objects.create(
            name="No Cap",
            alias="no_cap",
            max_amount=1000,
            weekly_cap_amount=0,
            min_threshold=50,
            priority_order=2,
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

    def test_admin_changelist_shows_weekly_info(self):
        """Test that admin changelist shows weekly cap information"""
        # Create some payments
        self._create_payment(self.recipient_with_cap, 200)

        response = self.client.get(
            reverse("admin:payment_instructions_paymentrecipient_changelist")
        )
        self.assertEqual(response.status_code, 200)

    def test_admin_changelist_shows_no_limit_for_zero_cap(self):
        """Test that admin changelist shows 'Sin límite' for recipients with no weekly cap"""
        response = self.client.get(
            reverse("admin:payment_instructions_paymentrecipient_changelist")
        )
        self.assertEqual(response.status_code, 200)

    def test_admin_detail_shows_weekly_info(self):
        """Test that admin detail view shows weekly cap information"""
        # Create payment
        self._create_payment(self.recipient_with_cap, 150)

        response = self.client.get(
            reverse(
                "admin:payment_instructions_paymentrecipient_change",
                args=[self.recipient_with_cap.id],
            )
        )
        self.assertEqual(response.status_code, 200)

        # Should show weekly cap related fields. La etiqueta dice "cupo consumido"
        # y no "recibido" porque incluye los pendientes: es cupo tomado, no plata
        # que entró.
        self.assertContains(response, "Cupo consumido esta semana")
        self.assertContains(response, "Restante esta semana")

        # Should show actual values
        self.assertContains(response, "$150")  # Current week received
        self.assertContains(response, "$250")  # Current week remaining (400 - 150)


class WeeklyCapAdminIntegrationTests(BaseTestCase):
    """Integration tests for weekly cap functionality in admin interface"""

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pass",
            role=User.ADMINISTRATOR,
            is_staff=True,
            is_superuser=True,
        )

        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )

        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )

        self.client = Client()
        self.client.login(username="admin", password="pass")

    def test_admin_workflow_create_recipient_with_weekly_cap(self):
        """Test complete admin workflow for creating recipient with weekly cap"""
        # Create recipient through admin
        form_data = {
            "name": "Integration Test",
            "alias": "integration_test",
            "cbu": "1" * 22,
            "recipient_type": "REGULAR",
            "max_amount": 2000,
            "weekly_cap_amount": 600,
            "min_threshold": 100,
            "priority_order": 1,
            "is_active": True,
        }

        response = self.client.post(
            reverse("admin:payment_instructions_paymentrecipient_add"), form_data, follow=True
        )

        self.assertEqual(response.status_code, 200)

        # Verify recipient was created correctly
        recipient = PaymentRecipient.objects.get(alias="integration_test")
        self.assertEqual(recipient.weekly_cap_amount, 600)

        # Test that weekly cap methods work
        self.assertEqual(recipient.get_remaining_weekly_amount(), 600)
        self.assertTrue(recipient.can_receive_amount(500))
        self.assertFalse(recipient.can_receive_amount(700))

    def test_admin_workflow_edit_weekly_cap(self):
        """Test editing weekly cap through admin interface"""
        # Create recipient
        recipient = PaymentRecipient.objects.create(
            name="Edit Test",
            alias="edit_test",
            max_amount=1000,
            weekly_cap_amount=400,
            min_threshold=50,
            priority_order=1,
        )

        # Edit through admin
        form_data = {
            "name": "Edit Test",
            "alias": "edit_test",
            "cbu": "1" * 22,
            "recipient_type": "REGULAR",
            "max_amount": 1000,
            "weekly_cap_amount": 500,  # Changed from 400 to 500
            "min_threshold": 50,
            "priority_order": 1,
            "is_active": True,
        }

        response = self.client.post(
            reverse("admin:payment_instructions_paymentrecipient_change", args=[recipient.id]),
            form_data,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        # Verify change was applied
        recipient.refresh_from_db()
        self.assertEqual(recipient.weekly_cap_amount, 500)

    def test_admin_workflow_disable_weekly_cap(self):
        """Test disabling weekly cap (setting to 0) through admin"""
        # Create recipient with weekly cap
        recipient = PaymentRecipient.objects.create(
            name="Disable Cap Test",
            alias="disable_cap_test",
            max_amount=1000,
            weekly_cap_amount=300,
            min_threshold=50,
            priority_order=1,
        )

        # Disable weekly cap through admin
        form_data = {
            "name": "Disable Cap Test",
            "alias": "disable_cap_test",
            "cbu": "1" * 22,
            "recipient_type": "REGULAR",
            "max_amount": 1000,
            "weekly_cap_amount": 0,  # Disable weekly cap
            "min_threshold": 50,
            "priority_order": 1,
            "is_active": True,
        }

        response = self.client.post(
            reverse("admin:payment_instructions_paymentrecipient_change", args=[recipient.id]),
            form_data,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        # Verify weekly cap was disabled
        recipient.refresh_from_db()
        self.assertEqual(recipient.weekly_cap_amount, 0)
        self.assertIsNone(recipient.get_remaining_weekly_amount())

    def test_admin_shows_weekly_cap_impact_on_payments(self):
        """Test that admin shows how weekly cap affects payment capacity"""
        # Create recipient with weekly cap
        recipient = PaymentRecipient.objects.create(
            name="Impact Test",
            alias="impact_test",
            max_amount=2000,
            weekly_cap_amount=500,
            min_threshold=50,
            priority_order=1,
        )

        # Create payment that uses part of weekly cap
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        Payment.objects.create(
            amount=300,
            payment_recipient=recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

        # Check admin detail view
        response = self.client.get(
            reverse("admin:payment_instructions_paymentrecipient_change", args=[recipient.id])
        )

        self.assertEqual(response.status_code, 200)

        # Should show current week received and remaining
        self.assertContains(response, "$300")  # Current week received
        self.assertContains(response, "$200")  # Current week remaining (500 - 300)
