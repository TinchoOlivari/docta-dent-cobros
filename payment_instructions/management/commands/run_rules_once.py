from django.core.management.base import BaseCommand
from django.utils import timezone
from payment_instructions.models import AutomationRule
from payment_instructions.tasks import (
    _should_run_today,
    _already_executed_today,
    _execute_rule_atomic,
)


class Command(BaseCommand):
    help = "Run automation rules once immediately (no Celery required)"

    def handle(self, *args, **options):
        now = timezone.localtime()
        rules = AutomationRule.objects.filter(is_active=True).select_related("payment_recipient")
        executed = 0
        for rule in rules:
            time_match = (
                rule.trigger_time.hour == now.hour and rule.trigger_time.minute == now.minute
            )
            if (
                _should_run_today(rule, now)
                and time_match
                and not _already_executed_today(rule, now)
            ):
                if _execute_rule_atomic(rule, now):
                    executed += 1
        self.stdout.write(self.style.SUCCESS(f"Executed {executed} rule(s)."))
