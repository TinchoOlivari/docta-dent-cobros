from django.test import TestCase
from django.contrib.admin.sites import AdminSite
from payment_instructions.admin import AutomationRuleAdmin
from payment_instructions.models import AutomationRule, PaymentRecipient


class AdminRulePreviewTests(TestCase):
    def test_admin_rule_preview(self):
        site = AdminSite()
        admin = AutomationRuleAdmin(AutomationRule, site)
        r = PaymentRecipient.objects.create(name="A", alias="A", max_amount=1000, priority_order=1)
        rule = AutomationRule(
            name="x",
            payment_recipient=r,
            rule_type=AutomationRule.RuleType.WEEKDAY,
            trigger_weekday=0,
            action_type=AutomationRule.ActionType.SET_PRIORITY,
            priority_position=AutomationRule.PriorityPosition.FIRST,
        )
        preview = admin.rule_preview(rule)
        self.assertIn("priority", preview.lower())
