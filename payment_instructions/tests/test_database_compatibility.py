"""
Tests de compatibilidad entre SQLite y PostgreSQL.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from payment_instructions.models import (
    AutomationRule,
    Payment,
    PaymentRecipient,
    RuleExecutionLog,
)

User = get_user_model()


def _create_user(username, role, password="testpass123"):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=password,
        role=role,
    )


class DatabaseConnectionTest(TestCase):
    def test_database_connection(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_database_vendor(self):
        self.assertIn(connection.vendor, ["sqlite", "postgresql"])


class ModelCRUDTest(TestCase):
    def setUp(self):
        self.operator = _create_user("operator_test", "operator")
        self.professional = _create_user("professional_test", "professional")
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient",
            alias="test-recipient",
            cbu="1234567890123456789012",
            max_amount=100000,
            min_threshold=1000,
        )

    def test_create_payment(self):
        payment = Payment.objects.create(
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.operator,
            amount=50000,
            patient_name="Test Patient",
        )
        self.assertIsNotNone(payment.pk)
        self.assertEqual(payment.amount, 50000)

    def test_update_payment(self):
        payment = Payment.objects.create(
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.operator,
            amount=50000,
            patient_name="Test Patient",
        )
        payment.observaciones = "Updated notes"
        payment.save()
        payment.refresh_from_db()
        self.assertEqual(payment.observaciones, "Updated notes")

    def test_delete_payment(self):
        payment = Payment.objects.create(
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.operator,
            amount=50000,
            patient_name="Test Patient",
        )
        payment_id = payment.pk
        payment.delete()
        self.assertFalse(Payment.objects.filter(pk=payment_id).exists())

    def test_query_with_relations(self):
        Payment.objects.create(
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.operator,
            amount=50000,
            patient_name="Test Patient",
        )
        payment = Payment.objects.select_related(
            "payment_recipient", "professional", "operator_user"
        ).first()
        self.assertEqual(payment.payment_recipient.alias, "test-recipient")
        self.assertEqual(payment.professional.username, "professional_test")


class DateTimeQueryTest(TestCase):
    def setUp(self):
        self.operator = _create_user("operator_dt", "operator")
        self.professional = _create_user("professional_dt", "professional")
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient DT",
            alias="test-recipient-dt",
            cbu="1234567890123456789012",
            max_amount=100000,
        )

    def test_filter_by_date_range(self):
        Payment.objects.create(
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.operator,
            amount=50000,
            patient_name="Test Patient",
        )
        now = timezone.now()
        payments = Payment.objects.filter(
            created_at__gte=now - timedelta(days=1),
            created_at__lte=now + timedelta(days=1),
        )
        self.assertEqual(payments.count(), 1)


class AggregationTest(TestCase):
    def setUp(self):
        self.operator = _create_user("operator_agg", "operator")
        self.professional = _create_user("professional_agg", "professional")
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient Agg",
            alias="test-recipient-agg",
            cbu="1234567890123456789012",
            max_amount=100000,
        )

    def test_sum_aggregation(self):
        from django.db.models import Sum

        for amount in (10000, 20000):
            Payment.objects.create(
                payment_recipient=self.recipient,
                professional=self.professional,
                operator_user=self.operator,
                amount=amount,
                patient_name="Patient",
            )
        total = Payment.objects.filter(payment_recipient=self.recipient).aggregate(
            total=Sum("amount")
        )["total"]
        self.assertEqual(total, 30000)


class TransactionTest(TransactionTestCase):
    def setUp(self):
        self.operator = _create_user("operator_tx", "operator")
        self.professional = _create_user("professional_tx", "professional")
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient TX",
            alias="test-recipient-tx",
            cbu="1234567890123456789012",
            max_amount=100000,
        )

    def test_atomic_transaction_commit(self):
        with transaction.atomic():
            payment = Payment.objects.create(
                payment_recipient=self.recipient,
                professional=self.professional,
                operator_user=self.operator,
                amount=50000,
                patient_name="Atomic Test",
            )
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())

    def test_atomic_transaction_rollback(self):
        try:
            with transaction.atomic():
                Payment.objects.create(
                    payment_recipient=self.recipient,
                    professional=self.professional,
                    operator_user=self.operator,
                    amount=50000,
                    patient_name="Rollback Test",
                )
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        self.assertFalse(Payment.objects.filter(patient_name="Rollback Test").exists())

    def test_select_for_update(self):
        with transaction.atomic():
            recipient = PaymentRecipient.objects.select_for_update().get(pk=self.recipient.pk)
            recipient.notes = "Updated with lock"
            recipient.save()
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.notes, "Updated with lock")


class WeeklyCapCalculationTest(TestCase):
    def setUp(self):
        self.operator = _create_user("operator_cap", "operator")
        self.professional = _create_user("professional_cap", "professional")
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient Cap",
            alias="test-recipient-cap",
            cbu="1234567890123456789012",
            max_amount=100000,
            weekly_cap_amount=50000,
        )

    def test_weekly_remaining_calculation(self):
        Payment.objects.create(
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.operator,
            amount=20000,
            patient_name="Patient 1",
        )
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 30000)

    def test_weekly_cap_exceeded(self):
        Payment.objects.create(
            payment_recipient=self.recipient,
            professional=self.professional,
            operator_user=self.operator,
            amount=50000,
            patient_name="Patient 1",
        )
        self.assertEqual(self.recipient.get_remaining_weekly_amount(), 0)


class AutomationRuleTest(TestCase):
    def setUp(self):
        self.recipient = PaymentRecipient.objects.create(
            name="Test Recipient Rule",
            alias="test-recipient-rule",
            cbu="1234567890123456789012",
            max_amount=100000,
        )

    def test_create_rule(self):
        rule = AutomationRule.objects.create(
            name="Test Rule",
            payment_recipient=self.recipient,
            rule_type=AutomationRule.RuleType.WEEKDAY,
            trigger_weekday=AutomationRule.Weekday.MONDAY,
            action_type=AutomationRule.ActionType.SET_ACTIVE_STATUS,
            active_value=True,
        )
        self.assertIsNotNone(rule.pk)

    def test_rule_execution_log(self):
        rule = AutomationRule.objects.create(
            name="Test Rule Log",
            payment_recipient=self.recipient,
            rule_type=AutomationRule.RuleType.WEEKDAY,
            trigger_weekday=AutomationRule.Weekday.MONDAY,
            action_type=AutomationRule.ActionType.SET_ACTIVE_STATUS,
            active_value=True,
        )
        log = RuleExecutionLog.objects.create(
            rule=rule,
            payment_recipient=self.recipient,
            action_taken="SET_ACTIVE_STATUS",
            old_value="False",
            new_value="True",
            success=True,
        )
        self.assertEqual(log.rule, rule)
