from django.db import migrations


def apagar(apps, schema_editor):
    """Apaga las tareas periódicas de payment_instructions (el beat nuevo no las corre).

    Si no existen, no hace nada. Revertir no las vuelve a prender (research R10).
    """
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(task__startswith="payment_instructions.").update(enabled=False)


class Migration(migrations.Migration):
    dependencies = [
        ("automatizacion", "0001_initial"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(apagar, migrations.RunPython.noop)]
