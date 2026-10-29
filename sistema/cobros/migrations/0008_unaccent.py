from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Búsqueda sin acentos (021/FR-022): `nombre__unaccent__icontains`."""

    dependencies = [("cobros", "0007_comprobantecobro")]

    operations = [UnaccentExtension()]
