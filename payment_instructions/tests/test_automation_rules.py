from django.test import TestCase
from payment_instructions.models import PaymentRecipient, AutomationRule
from payment_instructions.tasks import _apply_priority_action


class AutomationRulesPriorityTests(TestCase):
    def test_priority_first_sets_one(self):
        r = PaymentRecipient.objects.create(name="A", alias="A", max_amount=1000, priority_order=3)
        old, new = _apply_priority_action(r, AutomationRule.PriorityPosition.FIRST, None)
        self.assertEqual(new, 1)

    def test_priority_last_increments_max(self):
        a = PaymentRecipient.objects.create(name="A", alias="A", max_amount=1000, priority_order=1)
        PaymentRecipient.objects.create(name="B", alias="B", max_amount=1000, priority_order=2)
        old, new = _apply_priority_action(a, AutomationRule.PriorityPosition.LAST, None)
        self.assertEqual(new, 3)

    def test_priority_middle_sets_middle(self):
        PaymentRecipient.objects.create(name="A", alias="A", max_amount=1000, priority_order=1)
        b = PaymentRecipient.objects.create(name="B", alias="B", max_amount=1000, priority_order=2)
        old, new = _apply_priority_action(b, AutomationRule.PriorityPosition.MIDDLE, None)
        self.assertIn(new, [1, 2])

    def test_priority_specific_sets_value(self):
        r = PaymentRecipient.objects.create(name="A", alias="A", max_amount=1000, priority_order=1)
        old, new = _apply_priority_action(r, AutomationRule.PriorityPosition.SPECIFIC, 5)
        self.assertEqual(new, 5)
