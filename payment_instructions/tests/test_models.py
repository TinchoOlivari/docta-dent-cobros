import os
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from payment_instructions.models import (
    User,
    PaymentRecipient,
    Payment,
)
from payment_instructions.tests import BaseTestCase


class UserModelTests(BaseTestCase):
    def test_user_manager_create_user(self):
        user = User.objects.create_user(
            username="op1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.assertTrue(user.pk)
        self.assertTrue(user.check_password("pass"))
        self.assertEqual(user.role, User.OPERATOR)

    def test_user_manager_create_superuser(self):
        admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertEqual(admin.role, User.ADMINISTRATOR)

    def test_user_professional_role(self):
        """Test that professional role works correctly"""
        professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.assertEqual(professional.role, User.PROFESSIONAL)
        self.assertTrue(professional.is_professional())
        self.assertFalse(professional.is_operator())
        self.assertFalse(professional.is_administrator())

    def test_user_role_methods(self):
        """Test all role checking methods"""
        admin = User.objects.create_user(
            username="admin", email="admin@example.com", role=User.ADMINISTRATOR
        )
        operator = User.objects.create_user(
            username="operator", email="op@example.com", role=User.OPERATOR
        )
        professional = User.objects.create_user(
            username="professional", email="spec@example.com", role=User.PROFESSIONAL
        )

        # Test administrator
        self.assertTrue(admin.is_administrator())
        self.assertFalse(admin.is_operator())
        self.assertFalse(admin.is_professional())

        # Test operator
        self.assertFalse(operator.is_administrator())
        self.assertTrue(operator.is_operator())
        self.assertFalse(operator.is_professional())

        # Test professional
        self.assertFalse(professional.is_administrator())
        self.assertFalse(professional.is_operator())
        self.assertTrue(professional.is_professional())

    def test_user_get_current_month_amount_professional(self):
        """Test get_current_month_amount for professional users"""
        professional = User.objects.create_user(
            username="professional1", email="spec@example.com", role=User.PROFESSIONAL
        )
        # Should return 0 for professional with no payments
        self.assertEqual(professional.get_current_month_amount(), 0)

    def test_user_get_current_month_amount_non_professional(self):
        """Test get_current_month_amount for non-professional users"""
        operator = User.objects.create_user(
            username="operator1", email="op@example.com", role=User.OPERATOR
        )
        # Should return 0 for non-professional users
        self.assertEqual(operator.get_current_month_amount(), 0)


class PaymentRecipientValidationTests(BaseTestCase):
    def test_max_amount_must_be_non_negative(self):
        r = PaymentRecipient(
            name="Name",
            alias="alias1",
            cbu="1" * 22,
            max_amount=-1,
            min_threshold=0,
            priority_order=1,
        )
        with self.assertRaises(ValidationError):
            r.full_clean()

    def test_min_threshold_non_negative(self):
        r = PaymentRecipient(
            name="Name",
            alias="alias1",
            cbu="1" * 22,
            max_amount=100,
            min_threshold=-1,
            priority_order=1,
        )
        with self.assertRaises(ValidationError):
            r.full_clean()

    def test_priority_must_be_non_negative(self):
        r = PaymentRecipient(
            name="Name",
            alias="alias1",
            cbu="1" * 22,
            max_amount=100,
            min_threshold=0,
            priority_order=-1,
        )
        with self.assertRaises(ValidationError):
            r.full_clean()

    def test_min_threshold_cannot_exceed_max_amount(self):
        r = PaymentRecipient(
            name="Name",
            alias="alias1",
            cbu="1" * 22,
            max_amount=100,
            min_threshold=200,
            priority_order=1,
        )
        with self.assertRaises(ValidationError) as ctx:
            r.clean()
        self.assertIn("min_threshold", ctx.exception.error_dict)

    def test_cbu_non_digits(self):
        r = PaymentRecipient(
            name="Name",
            alias="alias2",
            cbu="a" * 22,  # not digits
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )
        with self.assertRaises(ValidationError) as ctx:
            r.clean()
        self.assertIn("cbu", ctx.exception.error_dict)

    def test_cbu_length_must_be_22_digits(self):
        r = PaymentRecipient(
            name="Name",
            alias="alias3",
            cbu="1" * 21,  # too short
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )
        with self.assertRaises(ValidationError) as ctx:
            r.clean()
        self.assertIn("cbu", ctx.exception.error_dict)

    def test_clean_allows_valid(self):
        r = PaymentRecipient(
            name="Valid",
            alias="valid",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=100,
            priority_order=1,
        )
        # should not raise
        r.clean()

    def test_cbu_can_be_null_and_not_unique_among_nulls(self):
        # Allow None CBU
        r1 = PaymentRecipient(
            name="R1",
            alias="r1",
            cbu=None,
            max_amount=100,
            min_threshold=0,
            priority_order=1,
        )
        r1.full_clean()
        r1.save()

        r2 = PaymentRecipient(
            name="R2",
            alias="r2",
            cbu=None,
            max_amount=100,
            min_threshold=0,
            priority_order=2,
        )
        # Should not raise despite unique=True because NULLs are allowed multiple times
        r2.full_clean()
        r2.save()

    def test_unique_alias_validation(self):
        PaymentRecipient.objects.create(
            name="R1", alias="dup", cbu="1" * 22, max_amount=10, min_threshold=0, priority_order=1
        )
        r = PaymentRecipient(
            name="R2", alias="dup", cbu="2" * 22, max_amount=10, min_threshold=0, priority_order=2
        )
        with self.assertRaises(ValidationError):
            r.full_clean()

    def test_unique_cbu_validation(self):
        PaymentRecipient.objects.create(
            name="R1", alias="dup1", cbu="1" * 22, max_amount=10, min_threshold=0, priority_order=1
        )
        r = PaymentRecipient(
            name="R2", alias="dup2", cbu="1" * 22, max_amount=10, min_threshold=0, priority_order=2
        )
        with self.assertRaises(ValidationError):
            r.full_clean()

    def test_notes_field_optional(self):
        """Test that notes field is optional and can be blank"""
        r = PaymentRecipient(
            name="Test",
            alias="test_notes",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
            notes="",  # Empty notes should be valid
        )
        r.full_clean()  # Should not raise ValidationError
        r.save()

        # Test with actual notes content
        r.notes = "This is a test note about the recipient"
        r.full_clean()  # Should not raise ValidationError
        r.save()

        # Verify notes are saved correctly
        r.refresh_from_db()
        self.assertEqual(r.notes, "This is a test note about the recipient")


class PaymentRecipientPriorityTests(BaseTestCase):
    def setUp(self):
        self.r1 = PaymentRecipient.objects.create(
            name="R1", alias="r1", cbu="1" * 22, max_amount=1000, min_threshold=0, priority_order=1
        )
        self.r2 = PaymentRecipient.objects.create(
            name="R2", alias="r2", cbu="2" * 22, max_amount=1000, min_threshold=0, priority_order=2
        )
        self.r3 = PaymentRecipient.objects.create(
            name="R3", alias="r3", cbu="3" * 22, max_amount=1000, min_threshold=0, priority_order=3
        )

    def test_new_inserts_shift_down(self):
        r = PaymentRecipient(
            name="R0", alias="r0", cbu="0" * 22, max_amount=1000, min_threshold=0, priority_order=1
        )
        r.save()
        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.r3.refresh_from_db()
        self.assertEqual(r.priority_order, 1)
        self.assertEqual(self.r1.priority_order, 2)
        self.assertEqual(self.r2.priority_order, 3)
        self.assertEqual(self.r3.priority_order, 4)

    def test_move_up_shifts_between(self):
        self.r3.priority_order = 1
        self.r3.save()
        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.r3.refresh_from_db()
        self.assertEqual(self.r3.priority_order, 1)
        self.assertEqual(self.r1.priority_order, 2)
        self.assertEqual(self.r2.priority_order, 3)

    def test_move_down_shifts_between(self):
        self.r1.priority_order = 3
        self.r1.save()
        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.r3.refresh_from_db()
        self.assertEqual(self.r2.priority_order, 1)
        self.assertEqual(self.r3.priority_order, 2)
        self.assertEqual(self.r1.priority_order, 3)

    def test_delete_reorders(self):
        self.r2.delete()
        self.r1.refresh_from_db()
        self.r3.refresh_from_db()
        self.assertEqual(self.r1.priority_order, 1)
        self.assertEqual(self.r3.priority_order, 2)


class PaymentRecipientCapacityTests(BaseTestCase):
    def setUp(self):
        self.recipient = PaymentRecipient.objects.create(
            name="R",
            alias="r",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=100,
            priority_order=1,
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.user = User.objects.create_user(username="op", password="pass", email="op@example.com")

    def _create_payment(self, amount):
        content = b"fake pdf content"
        file = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        return Payment.objects.create(
            amount=amount,
            payment_recipient=self.recipient,
            patient_name="Test Patient",
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.user,
        )

    def test_remaining_amount_and_capacity(self):
        self.assertEqual(self.recipient.get_remaining_amount(), 1000)
        p1 = self._create_payment(300)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.get_current_month_received(), 300)
        self.assertEqual(self.recipient.get_remaining_amount(), 700)
        self.assertFalse(self.recipient.is_completed_this_month())
        p2 = self._create_payment(700)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.get_remaining_amount(), 0)
        self.assertTrue(self.recipient.is_completed_this_month())

    def test_can_receive_amount_respects_min_threshold_and_active(self):
        self.recipient.is_active = False
        self.recipient.save()
        self.assertFalse(self.recipient.can_receive_amount(500))
        self.recipient.is_active = True
        self.recipient.save()
        self.assertFalse(self.recipient.can_receive_amount(50))  # below threshold 100
        self.assertTrue(self.recipient.can_receive_amount(100))

    def test_suggest_max_payment(self):
        self.assertEqual(self.recipient.suggest_max_payment(), 1000)

    def test_get_capacity_percentage(self):
        self.assertEqual(self.recipient.get_capacity_percentage(), 0.0)
        self._create_payment(250)
        self.recipient.refresh_from_db()
        self.assertAlmostEqual(self.recipient.get_capacity_percentage(), 25.0)

    def test_get_status(self):
        self.assertEqual(self.recipient.get_status(), "available")
        self.recipient.is_active = False
        self.recipient.save()
        self.assertEqual(self.recipient.get_status(), "inactive")

    def test_weekly_cap_enforcement(self):
        # Set a weekly cap and verify enforcement
        self.recipient.weekly_cap_amount = 400
        self.recipient.save()

        # First payment within week OK
        p1 = self._create_payment(300)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.get_current_week_received(), 300)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 100)

        # Next payment exceeding weekly remaining should be rejected
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            p2 = Payment(
                amount=150,
                payment_recipient=self.recipient,
                patient_name="Test Patient 2",
                professional=self.professional,
                proof_of_payment_file=SimpleUploadedFile(
                    "r2.pdf", b"content", content_type="application/pdf"
                ),
                operator_user=self.user,
            )
            p2.full_clean()

        # A payment exactly equal to remaining should pass
        p3 = Payment(
            amount=100,
            payment_recipient=self.recipient,
            patient_name="Test Patient 3",
            professional=self.professional,
            proof_of_payment_file=SimpleUploadedFile(
                "r3.pdf", b"content", content_type="application/pdf"
            ),
            operator_user=self.user,
        )
        p3.full_clean()
        p3.save()

        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.get_current_week_received(), 400)
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 0)

    def test_weekly_cap_validation_against_min_and_max(self):
        # weekly_cap_amount cannot exceed max_amount and cannot be less than min_threshold
        r = PaymentRecipient(
            name="W",
            alias="w",
            cbu="7" * 22,
            max_amount=500,
            min_threshold=200,
            priority_order=5,
            weekly_cap_amount=100,
        )
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError) as ctx:
            r.clean()
        self.assertIn("weekly_cap_amount", ctx.exception.error_dict)

        r.weekly_cap_amount = 600  # greater than max
        with self.assertRaises(ValidationError) as ctx2:
            r.clean()
        self.assertIn("weekly_cap_amount", ctx2.exception.error_dict)

        # Valid config
        r.weekly_cap_amount = 300  # between min_threshold(200) and max(500)
        # Should not raise
        r.clean()


class PaymentModelValidationTests(BaseTestCase):
    def setUp(self):
        self.recipient = PaymentRecipient.objects.create(
            name="R",
            alias="r",
            cbu="1" * 22,
            max_amount=500,
            min_threshold=100,
            priority_order=1,
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.user = User.objects.create_user(username="op", password="pass", email="op@example.com")

    def _file(self):
        return SimpleUploadedFile("test.pdf", b"content", content_type="application/pdf")

    def test_required_payment_recipient_field(self):
        p = Payment(
            amount=100,
            payment_recipient=None,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_required_professional_field(self):
        p = Payment(
            amount=100,
            payment_recipient=self.recipient,
            professional=None,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_amount_must_be_non_negative(self):
        p = Payment(
            amount=-1,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_requires_professional_and_proof_and_recipient(self):
        p = Payment(
            amount=100,
            payment_recipient=None,
            professional=None,
            proof_of_payment_file=None,
            operator_user=self.user,
            patient_name="Test Patient",
        )
        with self.assertRaises(ValidationError) as ctx:
            p.full_clean()
        err = ctx.exception.error_dict
        self.assertIn("professional", err)
        self.assertIn("payment_recipient", err)

    def test_cannot_exceed_remaining_amount(self):
        # First payment of 300 OK
        p1 = Payment(
            amount=300,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        p1.full_clean()
        p1.save()

        # Second payment of 300 exceeds remaining (500 - 300 = 200)
        p2 = Payment(
            amount=300,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        with self.assertRaises(ValidationError) as ctx:
            p2.full_clean()
        self.assertIn("amount", ctx.exception.error_dict)

    def test_editing_payment_excludes_self_from_capacity(self):
        p = Payment(
            amount=400,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        p.full_clean()
        p.save()

        # When editing, increasing to 500 should be allowed since remaining without counting self is 500
        p.amount = 500
        p.full_clean()  # should not raise

    def test_below_min_threshold_raises_on_clean(self):
        p = Payment(
            amount=50,  # below min_threshold=100
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        with self.assertRaises(ValidationError):
            p.clean()

    def test_professional_role_validation(self):
        """Test that only users with professional role can be assigned to payments"""
        # Create a non-professional user
        non_professional = User.objects.create_user(
            username="operator1", email="op@example.com", role=User.OPERATOR
        )

        p = Payment(
            amount=200,
            payment_recipient=self.recipient,
            professional=non_professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )

        with self.assertRaises(ValidationError) as ctx:
            p.clean()
        self.assertIn("professional", ctx.exception.error_dict)
        self.assertIn(
            "Solo usuarios con rol de profesional", str(ctx.exception.error_dict["professional"])
        )

    def test_professional_role_validation_passes_for_professional(self):
        """Test that professional role validation passes for actual professionals"""
        p = Payment(
            amount=200,
            payment_recipient=self.recipient,
            professional=self.professional,  # This is a professional user
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )

        # Should not raise ValidationError for professional role
        try:
            p.clean()
        except ValidationError as e:
            # If ValidationError is raised, it should not be about professional role
            self.assertNotIn("professional", e.error_dict)

    def test_get_monthly_totals(self):
        now = timezone.now()
        Payment.objects.create(
            amount=150,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        totals = Payment.get_monthly_totals(year=now.year, month=now.month)
        self.assertEqual(totals["total_amount"], 150)
        self.assertEqual(totals["payment_count"], 1)
        self.assertEqual(totals["unique_recipients"], 1)
        self.assertEqual(totals["unique_operators"], 1)

    def test_suggest_recipient_for_amount(self):
        # Create another recipient with higher priority but insufficient capacity
        low = PaymentRecipient.objects.create(
            name="Low",
            alias="low",
            cbu="9" * 22,
            max_amount=50,
            min_threshold=0,
            priority_order=1,
        )
        best = Payment.suggest_recipient_for_amount(100)
        self.assertEqual(best, self.recipient)

    def test_payment_str(self):
        p = Payment.objects.create(
            amount=123,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        s = str(p)
        self.assertIn("$123", s)
        self.assertIn(self.recipient.alias, s)

    def test_payment_clean_uses_weekly_cap_errors(self):
        # Set weekly cap to 250 and attempt over-the-cap payment
        self.recipient.weekly_cap_amount = 250
        self.recipient.save()
        # Create one payment of 200
        first = Payment(
            amount=200,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        first.full_clean()
        first.save()

        # Now try to create 100 more which exceeds weekly remaining (50)
        p = Payment(
            amount=100,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient 2",
        )
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError) as ctx:
            p.full_clean()
        # Must reference weekly limit message
        self.assertTrue(
            any(
                "semanal" in msg
                for msgs in ctx.exception.message_dict.values()
                for msg in (msgs if isinstance(msgs, list) else [msgs])
            )
        )


class UserProfessionalAggregationTests(BaseTestCase):
    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.recipient = PaymentRecipient.objects.create(
            name="R",
            alias="r",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )
        self.user = User.objects.create_user(username="op", password="pass", email="op@example.com")

    def _file(self):
        return SimpleUploadedFile("img.jpg", b"data", content_type="image/jpeg")

    def test_get_current_month_amount_professional(self):
        """Test get_current_month_amount for professional users"""
        start = self.professional.get_current_month_amount()
        self.assertEqual(start, 0)
        Payment.objects.create(
            amount=123,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=self.user,
            patient_name="Test Patient",
        )
        self.assertEqual(self.professional.get_current_month_amount(), 123)

    def test_get_current_month_amount_non_professional(self):
        """Test get_current_month_amount for non-professional users returns 0"""
        operator = User.objects.create_user(
            username="operator1", email="op@example.com", role=User.OPERATOR
        )
        # Even if we create a payment with this user as operator,
        # get_current_month_amount should return 0 for non-professionals
        Payment.objects.create(
            amount=200,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=self._file(),
            operator_user=operator,
            patient_name="Test Patient",
        )
        self.assertEqual(operator.get_current_month_amount(), 0)


class ManagerAndUtilitiesTests(BaseTestCase):
    def setUp(self):
        self.r1 = PaymentRecipient.objects.create(
            name="A1", alias="a1", cbu="1" * 22, max_amount=200, min_threshold=50, priority_order=1
        )
        self.r2 = PaymentRecipient.objects.create(
            name="A2", alias="a2", cbu="2" * 22, max_amount=100, min_threshold=0, priority_order=2
        )
        self.r3 = PaymentRecipient.objects.create(
            name="B1",
            alias="b1",
            cbu="3" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=3,
            is_active=False,
        )

    def test_get_available_recipients_filters_and_orders(self):
        qs = PaymentRecipient.objects.get_available_recipients(amount=60)
        self.assertEqual(
            [r.alias for r in qs], ["a1", "a2"]
        )  # r3 inactive excluded, order by priority then name

    def test_find_best_recipient(self):
        best = PaymentRecipient.objects.find_best_recipient(150)
        self.assertEqual(best, self.r1)


class FileUploadPathTests(BaseTestCase):
    def setUp(self):
        self.recipient = PaymentRecipient.objects.create(
            name="R",
            alias="aliasx",
            cbu="8" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.user = User.objects.create_user(
            username="opx", password="pass", email="opx@example.com"
        )

    def test_modify_file_name_uses_payment_id_and_uuid(self):
        """Test that filenames include Payment.id and UUID when created via two-phase save"""
        file = SimpleUploadedFile("receipt.pdf", b"data", content_type="application/pdf")

        # Create payment first without file to get ID
        p = Payment.objects.create(
            amount=10,
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.user,
            patient_name="Test Patient",
        )
        # Then assign file and save again
        p.proof_of_payment_file = file
        p.save(update_fields=["proof_of_payment_file"])

        # Check folder structure
        now = timezone.now()
        year = now.strftime("%Y")
        month = now.strftime("%m")
        self.assertTrue(p.proof_of_payment_file.name.startswith(f"comprobantes/{year}/{month}/"))

        # Check filename contains Payment.id and UUID
        filename = p.proof_of_payment_file.name.split("/")[-1]  # Get just the filename
        self.assertTrue(filename.startswith(f"{p.id}_"))

        # Check UUID is present (8 characters after payment_id_)
        parts = filename.split("_")
        self.assertEqual(len(parts), 2)  # payment_id and uuid.ext
        uuid_part = parts[1].split(".")[0]  # Remove extension
        self.assertEqual(len(uuid_part), 8)  # UUID should be 8 chars

        # Check extension is preserved
        self.assertTrue(filename.endswith(".pdf"))

    def test_modify_file_name_with_different_extensions(self):
        """Test filename generation with different file extensions"""
        extensions = [".jpg", ".png", ".pdf", ".jpeg"]

        for ext in extensions:
            with self.subTest(extension=ext):
                file = SimpleUploadedFile(f"test{ext}", b"data", content_type="image/jpeg")

                p = Payment.objects.create(
                    amount=10,
                    payment_recipient=self.recipient,
                    professional=self.professional,
                    operator_user=self.user,
                    patient_name="Test Patient",
                )
                p.proof_of_payment_file = file
                p.save(update_fields=["proof_of_payment_file"])

                filename = p.proof_of_payment_file.name.split("/")[-1]
                self.assertTrue(filename.endswith(ext))
                self.assertTrue(filename.startswith(f"{p.id}_"))

    def test_modify_file_name_fallback_to_tmp(self):
        """Test that filename uses 'tmp' when Payment.id is not available"""
        file = SimpleUploadedFile("receipt.pdf", b"data", content_type="application/pdf")

        # Create payment with file in single operation (no ID available during upload_to call)
        p = Payment.objects.create(
            amount=10,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.user,
            patient_name="Test Patient",
        )

        filename = p.proof_of_payment_file.name.split("/")[-1]
        # Should start with 'tmp_' when ID not available
        self.assertTrue(filename.startswith("tmp_"))

    def test_uuid_uniqueness_in_filenames(self):
        """Test that UUIDs in filenames are unique across multiple payments"""
        file1 = SimpleUploadedFile("receipt1.pdf", b"data1", content_type="application/pdf")
        file2 = SimpleUploadedFile("receipt2.pdf", b"data2", content_type="application/pdf")

        # Create two payments with proper two-phase save
        p1 = Payment.objects.create(
            amount=10,
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.user,
            patient_name="Test Patient 1",
        )
        p1.proof_of_payment_file = file1
        p1.save(update_fields=["proof_of_payment_file"])

        p2 = Payment.objects.create(
            amount=20,
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.user,
            patient_name="Test Patient 2",
        )
        p2.proof_of_payment_file = file2
        p2.save(update_fields=["proof_of_payment_file"])

        # Extract UUIDs from filenames
        filename1 = p1.proof_of_payment_file.name.split("/")[-1]
        filename2 = p2.proof_of_payment_file.name.split("/")[-1]

        uuid1 = filename1.split("_")[1].split(".")[0]
        uuid2 = filename2.split("_")[1].split(".")[0]

        # UUIDs should be different
        self.assertNotEqual(uuid1, uuid2)
        self.assertEqual(len(uuid1), 8)
        self.assertEqual(len(uuid2), 8)


class AdminFileUploadTests(BaseTestCase):
    def setUp(self):
        self.recipient = PaymentRecipient.objects.create(
            name="R",
            alias="test_alias",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.admin_user = User.objects.create_user(
            username="admin", password="pass", email="admin@example.com", role=User.ADMINISTRATOR
        )

    def test_admin_save_model_two_phase_save(self):
        """Test that admin save_model performs two-phase save for new payments with files"""
        from payment_instructions.admin import PaymentAdmin
        from django.contrib.admin.sites import AdminSite
        from django.http import HttpRequest

        # Create admin instance
        admin_site = AdminSite()
        payment_admin = PaymentAdmin(Payment, admin_site)

        # Mock request
        request = HttpRequest()
        request.user = self.admin_user

        # Create payment object with file
        file = SimpleUploadedFile("admin_test.pdf", b"admin data", content_type="application/pdf")
        payment = Payment(
            amount=100,
            patient_name="Test Patient",
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=file,
            operator_user=self.admin_user,
        )

        # Mock form (minimal mock)
        class MockForm:
            pass

        form = MockForm()

        # Call admin save_model (simulates admin form submission)
        payment_admin.save_model(request, payment, form, change=False)

        # Verify payment was saved and has an ID
        self.assertIsNotNone(payment.id)

        # Verify filename includes Payment.id and UUID
        filename = payment.proof_of_payment_file.name.split("/")[-1]
        self.assertTrue(filename.startswith(f"{payment.id}_"))

        # Verify UUID is present
        parts = filename.split("_")
        self.assertEqual(len(parts), 2)
        uuid_part = parts[1].split(".")[0]
        self.assertEqual(len(uuid_part), 8)

        # Verify folder structure
        now = timezone.now()
        year = now.strftime("%Y")
        month = now.strftime("%m")
        self.assertTrue(
            payment.proof_of_payment_file.name.startswith(f"comprobantes/{year}/{month}/")
        )

    def test_admin_save_model_existing_payment(self):
        """Test that admin save_model doesn't interfere with existing payments"""
        from payment_instructions.admin import PaymentAdmin
        from django.contrib.admin.sites import AdminSite
        from django.http import HttpRequest

        # Create existing payment
        file = SimpleUploadedFile("existing.pdf", b"existing data", content_type="application/pdf")
        payment = Payment.objects.create(
            amount=50,
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.admin_user,
            patient_name="Test Patient",
        )
        payment.proof_of_payment_file = file
        payment.save(update_fields=["proof_of_payment_file"])

        original_filename = payment.proof_of_payment_file.name

        # Create admin instance
        admin_site = AdminSite()
        payment_admin = PaymentAdmin(Payment, admin_site)

        # Mock request
        request = HttpRequest()
        request.user = self.admin_user

        # Mock form
        class MockForm:
            pass

        form = MockForm()

        # Update payment amount (change=True)
        payment.amount = 75
        payment_admin.save_model(request, payment, form, change=True)

        # Verify filename didn't change
        payment.refresh_from_db()
        self.assertEqual(payment.proof_of_payment_file.name, original_filename)
        self.assertEqual(payment.amount, 75)


class SummaryTests(BaseTestCase):
    def test_get_payment_summary_counts(self):
        # No recipients
        s = PaymentRecipient.get_payment_summary()
        self.assertEqual(s["total_recipients"], 0)
        self.assertEqual(s["active_recipients"], 0)

        # Add some
        r1 = PaymentRecipient.objects.create(
            name="R1", alias="r1s", cbu="1" * 22, max_amount=100, min_threshold=0, priority_order=1
        )
        PaymentRecipient.objects.create(
            name="R2",
            alias="r2s",
            cbu="2" * 22,
            max_amount=100,
            min_threshold=0,
            priority_order=2,
            is_active=False,
        )
        s = PaymentRecipient.get_payment_summary()
        self.assertEqual(s["total_recipients"], 2)
        self.assertEqual(s["active_recipients"], 1)


class PaymentFileDeletionTests(BaseTestCase):
    def setUp(self):
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test_recipient",
            cbu="1" * 22,
            max_amount=1000,
            min_threshold=0,
            priority_order=1,
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="spec@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.user = User.objects.create_user(
            username="test_user", password="pass", email="test@example.com"
        )

    def test_payment_deletion_removes_proof_file(self):
        """Test that deleting a Payment removes the proof_of_payment_file from filesystem"""
        # Create a test file
        file_content = b"test file content"
        test_file = SimpleUploadedFile(
            "test_receipt.pdf", file_content, content_type="application/pdf"
        )

        # Create payment with file
        payment = Payment.objects.create(
            amount=100,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=test_file,
            operator_user=self.user,
            patient_name="Test Patient",
        )

        # Verify file exists on filesystem
        file_path = payment.proof_of_payment_file.path
        self.assertTrue(os.path.isfile(file_path))

        # Delete the payment
        payment.delete()

        # Verify file was removed from filesystem
        self.assertFalse(os.path.isfile(file_path))

    def test_payment_deletion_without_file_does_not_error(self):
        """Test that deleting a Payment without a file doesn't cause errors"""
        # Create payment without file (this shouldn't happen in practice due to validation,
        # but we test the edge case)
        payment = Payment(
            amount=100,
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.user,
            patient_name="Test Patient",
        )
        # Save without validation to bypass required file field
        payment.save()

        # Delete should not raise any errors
        payment.delete()

    def test_payment_deletion_with_missing_file_does_not_error(self):
        """Test that deleting a Payment when the file is already missing doesn't cause errors"""
        # Create a test file
        file_content = b"test file content"
        test_file = SimpleUploadedFile(
            "test_receipt2.pdf", file_content, content_type="application/pdf"
        )

        # Create payment with file
        payment = Payment.objects.create(
            amount=100,
            payment_recipient=self.recipient,
            professional=self.professional,
            proof_of_payment_file=test_file,
            operator_user=self.user,
            patient_name="Test Patient",
        )

        # Manually remove the file from filesystem (simulating external deletion)
        file_path = payment.proof_of_payment_file.path
        if os.path.isfile(file_path):
            os.remove(file_path)

        # Verify file is gone
        self.assertFalse(os.path.isfile(file_path))

        # Delete the payment - should not raise any errors
        payment.delete()
