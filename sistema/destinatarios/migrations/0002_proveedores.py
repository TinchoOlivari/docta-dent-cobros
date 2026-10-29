from django.db import migrations

NOMBRES = ["Mercadopago", "Takataka", "Payway"]


def crear(apps, schema_editor):
    Proveedor = apps.get_model("destinatarios", "Proveedor")
    for n in NOMBRES:
        Proveedor.objects.get_or_create(nombre=n)


class Migration(migrations.Migration):
    dependencies = [("destinatarios", "0001_initial")]
    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
