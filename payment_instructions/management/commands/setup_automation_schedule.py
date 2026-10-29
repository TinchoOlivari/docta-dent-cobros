from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule
import json


class Command(BaseCommand):
    help = "Setup periodic task to execute automation rules"

    def handle(self, *args, **options):
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=1, period=IntervalSchedule.MINUTES
        )

        task, created = PeriodicTask.objects.get_or_create(
            name="Execute automation rules",
            defaults={
                "interval": schedule,
                "task": "payment_instructions.tasks.execute_automation_rules",
                "enabled": True,
                "kwargs": json.dumps({}),
            },
        )

        if not created:
            task.interval = schedule
            task.enabled = True
            task.task = "payment_instructions.tasks.execute_automation_rules"
            task.kwargs = json.dumps({})
            task.save()

        self.stdout.write(
            self.style.SUCCESS(
                "Periodic task configured: Execute automation rules (every 1 minute)"
            )
        )
