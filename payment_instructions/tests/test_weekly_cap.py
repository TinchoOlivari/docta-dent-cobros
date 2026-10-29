from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from datetime import timedelta
from zoneinfo import ZoneInfo

from payment_instructions.models import (
    User,
    PaymentRecipient,
    Payment,
)
from payment_instructions.tests import BaseTestCase


argentina_tz = ZoneInfo("America/Argentina/Buenos_Aires")


def get_now_arg():
    """Get current time in Argentina timezone"""
    return timezone.now().astimezone(argentina_tz)


class WeeklyCapModelTests(BaseTestCase):
    """Test weekly cap amount functionality in PaymentRecipient model"""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )

    def _create_recipient_with_weekly_cap(self, weekly_cap=500, max_amount=1000, min_threshold=100):
        """Helper to create recipient with weekly cap"""
        return PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test_recipient",
            cbu="1" * 22,
            max_amount=max_amount,
            weekly_cap_amount=weekly_cap,
            min_threshold=min_threshold,
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

    def test_weekly_cap_amount_field_defaults(self):
        """Test weekly_cap_amount field defaults and properties"""
        recipient = PaymentRecipient.objects.create(
            name="Test",
            alias="test",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )
        # Should default to 0 (no limit)
        self.assertEqual(recipient.weekly_cap_amount, 0)

    def test_weekly_cap_validation_cannot_exceed_max_amount(self):
        """Test that weekly_cap_amount cannot exceed max_amount"""
        recipient = PaymentRecipient(
            name="Test",
            alias="test",
            cbu="1" * 22,
            max_amount=500,
            weekly_cap_amount=600,  # Exceeds max_amount
            min_threshold=0,
            priority_order=1,
        )
        with self.assertRaises(ValidationError) as ctx:
            recipient.clean()
        self.assertIn("weekly_cap_amount", ctx.exception.error_dict)
        self.assertIn(
            "no puede superar el monto máximo", str(ctx.exception.error_dict["weekly_cap_amount"])
        )

    def test_weekly_cap_validation_cannot_be_less_than_min_threshold(self):
        """Test that weekly_cap_amount cannot be less than min_threshold"""
        recipient = PaymentRecipient(
            name="Test",
            alias="test",
            cbu="1" * 22,
            max_amount=1000,
            weekly_cap_amount=50,  # Less than min_threshold
            min_threshold=100,
            priority_order=1,
        )
        with self.assertRaises(ValidationError) as ctx:
            recipient.clean()
        self.assertIn("weekly_cap_amount", ctx.exception.error_dict)
        self.assertIn(
            "no puede ser menor al monto mínimo", str(ctx.exception.error_dict["weekly_cap_amount"])
        )

    def test_weekly_cap_validation_zero_means_no_limit(self):
        """Test that weekly_cap_amount of 0 means no limit and passes validation"""
        recipient = PaymentRecipient(
            name="Test",
            alias="test",
            cbu="1" * 22,
            max_amount=1000,
            weekly_cap_amount=0,  # No limit
            min_threshold=100,
            priority_order=1,
        )
        # Should not raise validation error
        recipient.clean()

    def test_weekly_cap_validation_valid_range(self):
        """Test that weekly_cap_amount in valid range passes validation"""
        recipient = PaymentRecipient(
            name="Test",
            alias="test",
            cbu="1" * 22,
            max_amount=1000,
            weekly_cap_amount=500,  # Between min_threshold and max_amount
            min_threshold=100,
            priority_order=1,
        )
        # Should not raise validation error
        recipient.clean()


class WeeklyCapCalculationTests(BaseTestCase):
    """Test weekly cap calculation methods"""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test_recipient",
            cbu="1" * 22,
            max_amount=2000,
            weekly_cap_amount=500,
            min_threshold=50,
            priority_order=1,
        )

    def _create_payment(self, amount, created_at=None):
        """Helper to create payment"""
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        payment = Payment.objects.create(
            amount=amount,
            payment_recipient=self.recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )
        if created_at:
            payment.created_at = created_at
            payment.save(update_fields=["created_at"])
        return payment

    def test_get_current_week_received_no_payments(self):
        """Test get_current_week_received with no payments"""
        self.assertEqual(self.recipient.get_current_week_received(), 0)

    def test_get_current_week_received_with_payments_this_week(self):
        """Test get_current_week_received with payments in current week"""
        # Create payments in current week
        self._create_payment(100)
        self._create_payment(150)

        self.assertEqual(self.recipient.get_current_week_received(), 250)

    def test_get_current_week_received_excludes_previous_week(self):
        """Test that get_current_week_received excludes payments from previous week"""
        now = get_now_arg()

        # Create payment in current week
        self._create_payment(100)

        # Create payment in previous week (8 days ago)
        previous_week = now - timedelta(days=8)
        self._create_payment(200, created_at=previous_week)

        # Should only count current week payment
        self.assertEqual(self.recipient.get_current_week_received(), 100)

    def test_get_current_week_received_excludes_next_week(self):
        """Test that get_current_week_received excludes payments from next week"""
        now = get_now_arg()

        # Create payment in current week
        self._create_payment(100)

        # Create payment in next week (8 days from now)
        next_week = now + timedelta(days=8)
        self._create_payment(200, created_at=next_week)

        # Should only count current week payment
        self.assertEqual(self.recipient.get_current_week_received(), 100)

    def test_get_current_week_received_week_boundaries(self):
        """Test that get_current_week_received correctly handles week boundaries (Monday to Sunday)"""
        now = get_now_arg()

        # Find the start of current week (Monday 00:00)
        start_of_week = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Find the end of current week (Sunday 23:59:59)
        end_of_week = start_of_week + timedelta(days=6, hours=23, minutes=59, seconds=59)

        # Create payments at week boundaries
        self._create_payment(100, created_at=start_of_week)  # Monday 00:00
        self._create_payment(150, created_at=end_of_week)  # Sunday 23:59:59

        # Both should be counted
        self.assertEqual(self.recipient.get_current_week_received(), 250)

        # Create payment just before week start (previous Sunday 23:59:59)
        before_week = start_of_week - timedelta(seconds=1)
        self._create_payment(200, created_at=before_week)

        # Should still be 250 (previous payment not counted)
        self.assertEqual(self.recipient.get_current_week_received(), 250)

    def test_get_current_week_received_exclude_payment(self):
        """Test get_current_week_received with exclude_payment parameter"""
        payment1 = self._create_payment(100)
        payment2 = self._create_payment(150)

        # Without exclusion
        self.assertEqual(self.recipient.get_current_week_received(), 250)

        # Exclude payment1
        self.assertEqual(self.recipient.get_current_week_received(exclude_payment=payment1), 150)

        # Exclude payment2
        self.assertEqual(self.recipient.get_current_week_received(exclude_payment=payment2), 100)

    def test_get_remaining_weekly_amount_no_cap(self):
        """Test get_remaining_weekly_amount when weekly cap is disabled (0)"""
        recipient = PaymentRecipient.objects.create(
            name="No Cap",
            alias="no_cap",
            cbu="2" * 22,
            max_amount=1000,
            weekly_cap_amount=0,  # No weekly cap
            min_threshold=0,
            priority_order=2,
        )

        # Should return None when no weekly cap
        self.assertIsNone(recipient.get_remaining_weekly_amount())

    def test_get_remaining_weekly_amount_with_cap(self):
        """Test get_remaining_weekly_amount with weekly cap enabled"""
        # No payments yet
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 500)

        # Create payment
        self._create_payment(200)

        # Should return remaining amount
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 300)

    def test_get_remaining_weekly_amount_cap_exceeded(self):
        """Test get_remaining_weekly_amount when cap is exceeded"""
        # Create payments that exceed weekly cap
        self._create_payment(300)
        self._create_payment(250)  # Total: 550, cap: 500

        # Should return negative amount
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), -50)

    def test_get_remaining_weekly_amount_exclude_payment(self):
        """Test get_remaining_weekly_amount with exclude_payment parameter"""
        payment1 = self._create_payment(200)
        payment2 = self._create_payment(150)

        # Without exclusion: 500 - 350 = 150
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 150)

        # Exclude payment1: 500 - 150 = 350
        self.assertEqual(self.recipient.get_remaining_weekly_amount(exclude_payment=payment1), 350)

        # Exclude payment2: 500 - 200 = 300
        self.assertEqual(self.recipient.get_remaining_weekly_amount(exclude_payment=payment2), 300)


class WeeklyCapPaymentValidationTests(BaseTestCase):
    """Test payment validation with weekly cap constraints"""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test_recipient",
            cbu="1" * 22,
            max_amount=2000,
            weekly_cap_amount=500,
            min_threshold=50,
            priority_order=1,
        )

    def _create_payment_obj(self, amount):
        """Helper to create payment object (not saved)"""
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        return Payment(
            amount=amount,
            payment_recipient=self.recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

    def _create_and_save_payment(self, amount):
        """Helper to create and save payment"""
        payment = self._create_payment_obj(amount)
        payment.save()
        return payment

    def test_payment_validation_within_weekly_cap(self):
        """Test that payments within weekly cap pass validation"""
        payment = self._create_payment_obj(300)  # Within 500 cap

        # Should not raise validation error
        payment.clean()

    def test_payment_validation_exceeds_weekly_cap_new_payment(self):
        """Test that new payment exceeding weekly cap fails validation"""
        # Create existing payment
        self._create_and_save_payment(300)

        # Try to create another payment that exceeds weekly cap
        payment = self._create_payment_obj(250)  # 300 + 250 = 550 > 500

        with self.assertRaises(ValidationError) as ctx:
            payment.clean()

        # Should mention weekly limit
        error_messages = str(ctx.exception.error_dict)
        self.assertIn("semanal", error_messages.lower())

    def test_payment_validation_exactly_weekly_cap(self):
        """Test that payment exactly reaching weekly cap passes validation"""
        # Create existing payment
        self._create_and_save_payment(300)

        # Create payment that exactly reaches weekly cap
        payment = self._create_payment_obj(200)  # 300 + 200 = 500 (exactly cap)

        # Should not raise validation error
        payment.clean()

    def test_payment_validation_weekly_cap_already_reached(self):
        """Test validation when weekly cap is already reached"""
        # Create payments that reach weekly cap
        self._create_and_save_payment(300)
        self._create_and_save_payment(200)  # Total: 500 (cap reached)

        # Try to create another payment
        payment = self._create_payment_obj(50)  # Even small amount should fail

        with self.assertRaises(ValidationError) as ctx:
            payment.clean()

        # Should mention that weekly limit is already reached
        error_messages = str(ctx.exception.error_dict)
        self.assertIn("límite semanal", error_messages.lower())

    def test_payment_validation_edit_existing_excludes_self(self):
        """Test that editing existing payment excludes itself from weekly cap calculation"""
        # Create payment
        payment = self._create_and_save_payment(400)

        # Edit payment to increase amount (should be allowed since we exclude self)
        payment.amount = 500  # Would exceed cap if self not excluded

        # Should not raise validation error
        payment.clean()

    def test_payment_validation_edit_existing_with_other_payments(self):
        """Test editing payment with other payments in the week"""
        # Create first payment
        payment1 = self._create_and_save_payment(200)

        # Create second payment
        payment2 = self._create_and_save_payment(200)  # Total: 400

        # Edit first payment to increase amount
        payment1.amount = 350  # New total would be 350 + 200 = 550 > 500

        with self.assertRaises(ValidationError) as ctx:
            payment1.clean()

        # Should mention weekly limit exceeded
        error_messages = str(ctx.exception.error_dict)
        self.assertIn("semanal", error_messages.lower())

    def test_payment_validation_no_weekly_cap_always_passes(self):
        """Test that payments always pass weekly validation when no cap is set"""
        recipient_no_cap = PaymentRecipient.objects.create(
            name="No Cap",
            alias="no_cap",
            cbu="2" * 22,
            max_amount=1000,
            weekly_cap_amount=0,  # No weekly cap
            min_threshold=0,
            priority_order=2,
        )

        # Create large payment (would exceed if cap was set)
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        payment = Payment(
            amount=800,
            payment_recipient=recipient_no_cap,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

        # Should not raise validation error
        payment.clean()


class WeeklyCapCanReceiveAmountTests(BaseTestCase):
    """Test can_receive_amount method with weekly cap constraints"""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test_recipient",
            cbu="1" * 22,
            max_amount=2000,
            weekly_cap_amount=500,
            min_threshold=50,
            priority_order=1,
        )

    def _create_and_save_payment(self, amount):
        """Helper to create and save payment"""
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        payment = Payment.objects.create(
            amount=amount,
            payment_recipient=self.recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )
        return payment

    def test_can_receive_amount_within_weekly_cap(self):
        """Test can_receive_amount returns True when amount is within weekly cap"""
        self.assertTrue(self.recipient.can_receive_amount(300))

    def test_can_receive_amount_exceeds_weekly_cap(self):
        """Test can_receive_amount returns False when amount exceeds weekly cap"""
        # Create existing payment
        self._create_and_save_payment(300)

        # Try amount that would exceed weekly cap
        self.assertFalse(self.recipient.can_receive_amount(250))  # 300 + 250 = 550 > 500

    def test_can_receive_amount_exactly_weekly_cap(self):
        """Test can_receive_amount returns True when amount exactly reaches weekly cap"""
        # Create existing payment
        self._create_and_save_payment(300)

        # Try amount that exactly reaches weekly cap
        self.assertTrue(self.recipient.can_receive_amount(200))  # 300 + 200 = 500 (exactly cap)

    def test_can_receive_amount_weekly_cap_already_reached(self):
        """Test can_receive_amount returns False when weekly cap is already reached"""
        # Create payments that reach weekly cap
        self._create_and_save_payment(300)
        self._create_and_save_payment(200)  # Total: 500 (cap reached)

        # Any additional amount should be rejected
        self.assertFalse(self.recipient.can_receive_amount(1))

    def test_can_receive_amount_no_weekly_cap(self):
        """Test can_receive_amount ignores weekly cap when it's disabled (0)"""
        recipient_no_cap = PaymentRecipient.objects.create(
            name="No Cap",
            alias="no_cap",
            cbu="2" * 22,
            max_amount=1000,
            weekly_cap_amount=0,  # No weekly cap
            min_threshold=0,
            priority_order=2,
        )

        # Should only be limited by monthly cap and min threshold
        self.assertTrue(recipient_no_cap.can_receive_amount(800))

    def test_can_receive_amount_weekly_vs_monthly_cap_precedence(self):
        """Test that can_receive_amount respects both weekly and monthly caps"""
        # Set up recipient where weekly cap is more restrictive than monthly
        recipient = PaymentRecipient.objects.create(
            name="Restrictive Weekly",
            alias="restrictive",
            cbu="3" * 22,
            max_amount=2000,  # Monthly cap
            weekly_cap_amount=300,  # Weekly cap (more restrictive)
            min_threshold=0,
            priority_order=3,
        )

        # Amount within monthly but exceeds weekly
        self.assertFalse(recipient.can_receive_amount(400))  # Exceeds weekly cap of 300

        # Amount within weekly cap
        self.assertTrue(recipient.can_receive_amount(250))

    def test_can_receive_amount_exclude_payment_parameter(self):
        """Test can_receive_amount with exclude_payment parameter"""
        payment1 = self._create_and_save_payment(300)
        payment2 = self._create_and_save_payment(150)  # Total: 450

        # Without exclusion, remaining weekly: 50
        self.assertFalse(self.recipient.can_receive_amount(100))

        # Exclude payment1, remaining weekly: 350
        self.assertTrue(self.recipient.can_receive_amount(100, exclude_payment=payment1))

        # Exclude payment2, remaining weekly: 200
        self.assertTrue(self.recipient.can_receive_amount(100, exclude_payment=payment2))

    def test_can_receive_amount_respects_min_threshold_with_weekly_cap(self):
        """Test that can_receive_amount still respects min_threshold with weekly cap"""
        # Amount below min_threshold should be rejected even if within weekly cap
        self.assertFalse(self.recipient.can_receive_amount(30))  # Below min_threshold of 50

        # Amount above min_threshold and within weekly cap should be accepted
        self.assertTrue(self.recipient.can_receive_amount(100))

    def test_can_receive_amount_inactive_recipient(self):
        """Test that can_receive_amount returns False for inactive recipients"""
        self.recipient.is_active = False
        self.recipient.save()

        # Should return False regardless of weekly cap
        self.assertFalse(self.recipient.can_receive_amount(100))


class WeeklyCapManagerTests(BaseTestCase):
    """Test PaymentRecipientManager methods with weekly cap constraints"""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )

        # Create recipients with different weekly caps
        self.recipient_no_cap = PaymentRecipient.objects.create(
            name="No Cap",
            alias="no_cap",
            cbu="1" * 22,
            max_amount=1000,
            weekly_cap_amount=0,  # No weekly cap
            min_threshold=0,
            priority_order=1,
        )

        self.recipient_low_cap = PaymentRecipient.objects.create(
            name="Low Cap",
            alias="low_cap",
            cbu="2" * 22,
            max_amount=1000,
            weekly_cap_amount=200,  # Low weekly cap
            min_threshold=0,
            priority_order=2,
        )

        self.recipient_high_cap = PaymentRecipient.objects.create(
            name="High Cap",
            alias="high_cap",
            cbu="3" * 22,
            max_amount=1000,
            weekly_cap_amount=800,  # High weekly cap
            min_threshold=0,
            priority_order=3,
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

    def test_get_available_recipients_filters_by_weekly_cap(self):
        """Test that get_available_recipients filters recipients based on weekly cap"""
        # Create payments that affect weekly caps
        self._create_payment(self.recipient_low_cap, 150)  # Remaining weekly: 50

        # Request amount that exceeds low cap remaining but fits others
        available = PaymentRecipient.objects.get_available_recipients(amount=100)

        # Should exclude recipient_low_cap due to insufficient weekly remaining
        self.assertNotIn(self.recipient_low_cap, available)
        self.assertIn(self.recipient_no_cap, available)
        self.assertIn(self.recipient_high_cap, available)

    def test_get_available_recipients_includes_recipients_with_sufficient_weekly_cap(self):
        """Test that get_available_recipients includes recipients with sufficient weekly cap"""
        # Request amount that fits within all weekly caps
        available = PaymentRecipient.objects.get_available_recipients(amount=100)

        # Should include all active recipients
        self.assertIn(self.recipient_no_cap, available)
        self.assertIn(self.recipient_low_cap, available)
        self.assertIn(self.recipient_high_cap, available)

    def test_find_best_recipient_considers_weekly_cap(self):
        """Test that find_best_recipient considers weekly cap constraints"""
        # Create payment that makes low cap recipient unavailable
        self._create_payment(self.recipient_low_cap, 180)  # Remaining weekly: 20

        # Request amount that exceeds low cap remaining
        best = PaymentRecipient.objects.find_best_recipient(amount=100)

        # Should not return the low cap recipient
        self.assertNotEqual(best, self.recipient_low_cap)
        # Should return one of the available recipients (no_cap has higher priority)
        self.assertEqual(best, self.recipient_no_cap)

    def test_find_best_recipient_no_available_due_to_weekly_caps(self):
        """Test find_best_recipient when no recipients available due to weekly caps"""
        # Fill up all recipients' weekly caps
        self._create_payment(self.recipient_low_cap, 200)  # Weekly cap reached
        self._create_payment(self.recipient_high_cap, 800)  # Weekly cap reached
        # no_cap recipient has no weekly limit, so create monthly limit payment
        for _ in range(10):
            self._create_payment(self.recipient_no_cap, 100)  # Total: 1000, monthly cap reached

        # Request any amount
        best = PaymentRecipient.objects.find_best_recipient(amount=50)

        # Should return None since no recipients available
        self.assertIsNone(best)


class WeeklyCapIntegrationTests(BaseTestCase):
    """Integration tests for weekly cap functionality across the system"""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.recipient = PaymentRecipient.objects.create(
            name="Integration Test",
            alias="integration",
            cbu="1" * 22,
            max_amount=2000,
            weekly_cap_amount=600,
            min_threshold=50,
            priority_order=1,
        )

    def _create_payment(self, amount):
        """Helper to create payment"""
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        return Payment.objects.create(
            amount=amount,
            payment_recipient=self.recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

    def test_weekly_cap_full_workflow(self):
        """Test complete workflow with weekly cap constraints"""
        # Initial state - should be able to receive full weekly cap
        self.assertTrue(self.recipient.can_receive_amount(600))
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 600)

        # Create first payment
        payment1 = self._create_payment(250)
        self.assertEqual(self.recipient.get_current_week_received(), 250)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 350)
        self.assertTrue(self.recipient.can_receive_amount(300))
        self.assertFalse(self.recipient.can_receive_amount(400))

        # Create second payment
        payment2 = self._create_payment(200)
        self.assertEqual(self.recipient.get_current_week_received(), 450)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 150)
        self.assertTrue(self.recipient.can_receive_amount(150))
        self.assertFalse(self.recipient.can_receive_amount(200))

        # Create final payment to reach cap
        payment3 = self._create_payment(150)
        self.assertEqual(self.recipient.get_current_week_received(), 600)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 0)
        self.assertFalse(self.recipient.can_receive_amount(1))

        # Verify manager methods respect the cap
        available = PaymentRecipient.objects.get_available_recipients(amount=50)
        self.assertNotIn(self.recipient, available)

        best = PaymentRecipient.objects.find_best_recipient(amount=50)
        self.assertNotEqual(best, self.recipient)

    def test_weekly_cap_with_payment_editing(self):
        """Test weekly cap behavior when editing existing payments"""
        # Create initial payment
        payment = self._create_payment(300)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 300)

        # Edit payment to increase amount (should work since we exclude self)
        payment.amount = 500
        payment.clean()  # Should not raise
        payment.save()

        self.assertEqual(self.recipient.get_current_week_received(), 500)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 100)

        # Try to edit beyond weekly cap
        payment.amount = 650  # Would exceed cap even excluding self

        # Create another payment first
        other_payment = self._create_payment(50)  # Total would be 650 + 50 = 700 > 600

        with self.assertRaises(ValidationError):
            payment.clean()

    def test_weekly_cap_cross_week_behavior(self):
        """Test that weekly cap resets across week boundaries"""
        now = get_now_arg()

        # Create payment in current week
        payment1 = self._create_payment(500)
        self.assertEqual(self.recipient.get_current_week_received(), 500)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 100)

        # Simulate payment from previous week (should not affect current week calculation)
        previous_week = now - timedelta(days=8)
        payment2 = self._create_payment(400)
        payment2.created_at = previous_week
        payment2.save(update_fields=["created_at"])

        # Current week calculation should be unchanged
        self.assertEqual(self.recipient.get_current_week_received(), 500)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 100)

        # Should still be able to receive remaining weekly amount
        self.assertTrue(self.recipient.can_receive_amount(100))
        self.assertFalse(self.recipient.can_receive_amount(150))


class WeeklyCapEdgeCasesTests(BaseTestCase):
    """Test edge cases and corner scenarios for weekly cap functionality"""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )

    def test_weekly_cap_equal_to_min_threshold(self):
        """Test weekly cap equal to min threshold"""
        recipient = PaymentRecipient.objects.create(
            name="Edge Case",
            alias="edge",
            cbu="1" * 22,
            max_amount=1000,
            weekly_cap_amount=100,
            min_threshold=100,  # Same as weekly cap
            priority_order=1,
        )

        # Should be able to make exactly one payment per week
        self.assertTrue(recipient.can_receive_amount(100))

        # Create the payment
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        Payment.objects.create(
            amount=100,
            payment_recipient=recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

        # Should not be able to make another payment this week
        self.assertFalse(recipient.can_receive_amount(100))

    def test_weekly_cap_larger_than_monthly_cap(self):
        """Test weekly cap larger than monthly cap (should be validated)"""
        recipient = PaymentRecipient(
            name="Invalid",
            alias="invalid",
            cbu="1" * 22,
            max_amount=500,  # Monthly cap
            weekly_cap_amount=800,  # Larger than monthly cap
            min_threshold=0,
            priority_order=1,
        )

        with self.assertRaises(ValidationError) as ctx:
            recipient.clean()
        self.assertIn("weekly_cap_amount", ctx.exception.error_dict)

    def test_weekly_cap_with_zero_max_amount(self):
        """Test weekly cap with zero max amount (edge case)"""
        recipient = PaymentRecipient(
            name="Zero Max",
            alias="zero_max",
            cbu="1" * 22,
            max_amount=0,
            weekly_cap_amount=100,  # Should fail validation
            min_threshold=0,
            priority_order=1,
        )

        with self.assertRaises(ValidationError) as ctx:
            recipient.clean()
        self.assertIn("weekly_cap_amount", ctx.exception.error_dict)

    def test_negative_weekly_cap_validation(self):
        """Test that negative weekly cap values are handled by field validation"""
        # PositiveIntegerField should prevent negative values at the field level
        recipient = PaymentRecipient(
            name="Negative",
            alias="negative",
            cbu="1" * 22,
            max_amount=1000,
            weekly_cap_amount=-100,  # Should fail field validation
            min_threshold=0,
            priority_order=1,
        )

        with self.assertRaises(ValidationError):
            recipient.full_clean()

    def test_weekly_cap_with_multiple_recipients_same_week(self):
        """Test weekly cap calculations with multiple recipients in same week"""
        recipient1 = PaymentRecipient.objects.create(
            name="Recipient 1",
            alias="r1",
            cbu="1" * 22,
            max_amount=1000,
            weekly_cap_amount=300,
            min_threshold=0,
            priority_order=1,
        )

        recipient2 = PaymentRecipient.objects.create(
            name="Recipient 2",
            alias="r2",
            cbu="2" * 22,
            max_amount=1000,
            weekly_cap_amount=400,
            min_threshold=0,
            priority_order=2,
        )

        # Create payments for both recipients
        content = b"fake pdf content"
        file1 = SimpleUploadedFile("test1.pdf", content, content_type="application/pdf")
        file2 = SimpleUploadedFile("test2.pdf", content, content_type="application/pdf")

        Payment.objects.create(
            amount=200,
            payment_recipient=recipient1,
            patient_name="Patient 1",
            professional=self.professional,
            proof_of_payment_file=file1,
            operator_user=self.operator,
        )

        Payment.objects.create(
            amount=250,
            payment_recipient=recipient2,
            patient_name="Patient 2",
            professional=self.professional,
            proof_of_payment_file=file2,
            operator_user=self.operator,
        )

        # Each recipient should track their own weekly amounts independently
        self.assertEqual(recipient1.get_current_week_received(), 200)
        self.assertEqual(recipient1.get_remaining_weekly_amount(), 100)

        self.assertEqual(recipient2.get_current_week_received(), 250)
        self.assertEqual(recipient2.get_remaining_weekly_amount(), 150)

    def test_weekly_cap_timezone_handling(self):
        """Test that weekly cap calculations handle timezone correctly"""
        recipient = PaymentRecipient.objects.create(
            name="Timezone Test",
            alias="timezone",
            cbu="1" * 22,
            max_amount=1000,
            weekly_cap_amount=500,
            min_threshold=0,
            priority_order=1,
        )

        # Create payment with specific timezone-aware datetime
        now = get_now_arg()
        start_of_week = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        payment = Payment.objects.create(
            amount=300,
            payment_recipient=recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.operator,
        )

        # Manually set created_at to start of week
        payment.created_at = start_of_week
        payment.save(update_fields=["created_at"])

        # Should be counted in current week
        self.assertEqual(recipient.get_current_week_received(), 300)
        self.assertEqual(recipient.get_remaining_weekly_amount(), 200)
