from django.core.management.base import BaseCommand, CommandError

from sistema.cobros.models import Cobro

# corte.sh distingue "hay cobros nuevos" (este código) de cualquier otro error (código 1).
HAY_COBROS = 3


class Command(BaseCommand):
    help = "Dice si se puede volver al sistema viejo: ningún cobro cargado en el sistema nuevo."

    def handle(self, *args, **opciones):
        n = Cobro.objects.filter(migrado=False).count()
        if n:
            raise CommandError(
                f"Cobros cargados en el sistema nuevo: {n}. No se vuelve atrás (FR-014).",
                returncode=HAY_COBROS,
            )
        self.stdout.write("Cobros cargados en el sistema nuevo: 0. Se puede volver atrás.")
