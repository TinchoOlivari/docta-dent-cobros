from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch
from payment_instructions.models import PaymentRecipient, AutomationRule, RuleExecutionLog
from payment_instructions.tasks import execute_automation_rules


class TaskExecutionTests(TestCase):
    @patch("payment_instructions.tasks.timezone.localtime")
    def test_execute_weekday_rule(self, mocked_localtime):
        # Freeze time to Monday 00:00
        mocked_localtime.return_value = timezone.datetime(
            2025, 1, 6, 0, 0, tzinfo=timezone.get_current_timezone()
        )

        r = PaymentRecipient.objects.create(name="A", alias="A", max_amount=1000, priority_order=5)
        rule = AutomationRule.objects.create(
            name="Mon first",
            payment_recipient=r,
            is_active=True,
            rule_type=AutomationRule.RuleType.WEEKDAY,
            trigger_weekday=0,
            trigger_time=timezone.datetime.strptime("00:00", "%H:%M").time(),
            action_type=AutomationRule.ActionType.SET_PRIORITY,
            priority_position=AutomationRule.PriorityPosition.FIRST,
        )

        execute_automation_rules()
        r.refresh_from_db()
        self.assertEqual(r.priority_order, 1)
        self.assertTrue(RuleExecutionLog.objects.filter(rule=rule, success=True).exists())
