from django.db import migrations


def crear_topes(apps, schema_editor):
    Cuenta = apps.get_model("destinatarios", "Cuenta")
    TopeVigente = apps.get_model("destinatarios", "TopeVigente")
    for c in Cuenta.objects.filter(tipo="transferencia"):
        for tipo in ("mensual", "semanal"):
            TopeVigente.objects.create(
                cuenta=c, tipo=tipo, monto=getattr(c, f"tope_{tipo}"), desde=c.fecha_alta
            )


class Migration(migrations.Migration):
    dependencies = [("destinatarios", "0007_topevigente")]

    operations = [migrations.RunPython(crear_topes, migrations.RunPython.noop)]
