from django.db import migrations, models
from django.db.models import F


def copiar_fecha_alta(apps, schema_editor):
    Cuenta = apps.get_model("destinatarios", "Cuenta")
    Cuenta.objects.filter(tipo="terminal_pos").update(acumula_desde=F("fecha_alta"))


class Migration(migrations.Migration):

    dependencies = [
        ("destinatarios", "0004_cuenta_posicion"),
    ]

    operations = [
        migrations.AddField(
            model_name="cuenta",
            name="acumula_desde",
            field=models.DateTimeField(blank=True, null=True, verbose_name="acumula desde"),
        ),
        migrations.RunPython(copiar_fecha_alta, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="cuenta",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("tipo", "terminal_pos"), ("acumula_desde__isnull", False)),
                    models.Q(
                        models.Q(("tipo", "terminal_pos"), _negated=True),
                        ("acumula_desde__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="cuenta_pos_acumula_desde",
                violation_error_message="Falta «acumula desde» de la cuenta de terminal POS.",
            ),
        ),
    ]
