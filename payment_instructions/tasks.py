import logging
from celery import shared_task
from django.db import transaction, connection
from django.utils import timezone
from django.db.models import Max
from .models import AutomationRule, RuleExecutionLog, PaymentRecipient

logger = logging.getLogger(__name__)


def _last_day_of_month(dt: timezone.datetime) -> int:
    if dt.month == 12:
        next_month = dt.replace(year=dt.year + 1, month=1, day=1)
    else:
        next_month = dt.replace(month=dt.month + 1, day=1)
    last_day = (next_month - timezone.timedelta(days=1)).day
    return last_day


def _should_run_today(rule, now):
    if rule.rule_type == AutomationRule.RuleType.WEEKDAY:
        return now.weekday() == rule.trigger_weekday
    if rule.rule_type == AutomationRule.RuleType.MONTHDAY:
        # Execute on the min(trigger_day, last_day_of_month)
        last_day = _last_day_of_month(now)
        expected_day = min(rule.trigger_monthday or 1, last_day)
        return now.day == expected_day
    return False


def _already_executed_today(rule, now):
    if not rule.last_executed:
        return False
    return rule.last_executed.astimezone(now.tzinfo).date() == now.date()


def _apply_priority_action(recipient: PaymentRecipient, position: str, value: int | None):
    if position == AutomationRule.PriorityPosition.FIRST:
        new_priority = 1
    elif position == AutomationRule.PriorityPosition.LAST:
        max_priority = PaymentRecipient.objects.aggregate(maxp=Max("priority_order"))["maxp"] or 0
        new_priority = max_priority + 1
    elif position == AutomationRule.PriorityPosition.MIDDLE:
        max_priority = PaymentRecipient.objects.aggregate(maxp=Max("priority_order"))["maxp"] or 1
        new_priority = max(1, (max_priority + 1) // 2)
    elif position == AutomationRule.PriorityPosition.SPECIFIC:
        if not value or value < 1:
            raise ValueError("Invalid specific priority value")
        new_priority = int(value)
    else:
        raise ValueError("Unknown priority position")

    old_priority = recipient.priority_order
    if old_priority == new_priority:
        return old_priority, new_priority

    recipient.priority_order = new_priority
    recipient._skip_priority_validation = True
    recipient.save()
    return old_priority, new_priority


def _execute_rule_atomic(rule: AutomationRule, now: timezone.datetime):
    with transaction.atomic():
        # Ensure we don't hold stale locks from previous operations
        try:
            if connection.vendor == "sqlite":
                with connection.cursor() as c:
                    c.execute("PRAGMA busy_timeout=5000;")
        except Exception:
            pass
        recipient = rule.payment_recipient
        if not PaymentRecipient.objects.filter(pk=recipient.pk).exists():
            RuleExecutionLog.objects.create(
                rule=rule,
                payment_recipient=recipient,
                action_taken="Recipient missing, skipped",
                success=False,
                error_message="Recipient no longer exists",
            )
            return False

        if rule.action_type == AutomationRule.ActionType.SET_ACTIVE_STATUS:
            old_value = str(recipient.is_active)
            recipient.is_active = bool(rule.active_value)
            recipient.save(update_fields=["is_active"])
            RuleExecutionLog.objects.create(
                rule=rule,
                payment_recipient=recipient,
                action_taken="SET_ACTIVE_STATUS",
                old_value=old_value,
                new_value=str(recipient.is_active),
                success=True,
            )
        elif rule.action_type == AutomationRule.ActionType.SET_PRIORITY:
            old_p, new_p = _apply_priority_action(
                recipient,
                rule.priority_position,
                rule.priority_value,
            )
            RuleExecutionLog.objects.create(
                rule=rule,
                payment_recipient=recipient,
                action_taken=f"SET_PRIORITY:{rule.priority_position}",
                old_value=str(old_p),
                new_value=str(new_p),
                success=True,
            )
        else:
            raise ValueError("Unsupported action type")

        rule.last_executed = now
        rule.save(update_fields=["last_executed"])
        return True


@shared_task(autoretry_for=(Exception,), retry_backoff=5, retry_kwargs={"max_retries": 3})
def execute_automation_rules():
    now = timezone.localtime()
    rules = AutomationRule.objects.filter(is_active=True).select_related("payment_recipient")

    executed_count = 0
    logger.info(
        "Automation rules execution started at %s (total active rules=%d)",
        now.isoformat(),
        rules.count(),
    )

    for rule in rules:
        try:
            should_run = _should_run_today(rule, now)
            time_match = (
                rule.trigger_time.hour == now.hour and rule.trigger_time.minute == now.minute
            )
            if not (should_run and time_match):
                continue

            if _already_executed_today(rule, now):
                continue

            if _execute_rule_atomic(rule, now):
                executed_count += 1

        except Exception as exc:
            logger.exception("Error executing rule %s (%s)", rule.id, rule.name)
            RuleExecutionLog.objects.create(
                rule=rule,
                payment_recipient=rule.payment_recipient,
                action_taken="ERROR",
                success=False,
                error_message=str(exc),
            )

    logger.info("Automation rules execution finished. Executed=%d", executed_count)
    return f"done:{executed_count}"


def execute_single_rule(rule_id: int):
    now = timezone.localtime()
    rule = AutomationRule.objects.select_related("payment_recipient").get(pk=rule_id)
    _execute_rule_atomic(rule, now)
    return True
