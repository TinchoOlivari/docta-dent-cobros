from django.core.management.base import BaseCommand, CommandError

from sistema.migracion.servicios import leer_datos, migrar, verificar


class Command(BaseCommand):
    help = "Migra los datos de prod al sistema nuevo. Por defecto solo revisa y deshace todo."

    def add_arguments(self, parser):
        parser.add_argument("--datos", required=True, help="Ruta del JSON de datos.")
        parser.add_argument(
            "--definitiva",
            action="store_true",
            help="Escribe de verdad. Sin este flag es una revisión que deshace todo.",
        )
        parser.add_argument(
            "--verificar",
            action="store_true",
            help="Solo compara prod con lo migrado. No escribe nada.",
        )

    def handle(self, *args, **opciones):
        if opciones["verificar"] and opciones["definitiva"]:
            raise CommandError("Elegí --verificar o --definitiva, no los dos.")
        datos = leer_datos(opciones["datos"])
        if opciones["verificar"]:
            lineas = verificar(datos)
            if not lineas:
                self.stdout.write("Verificación: 0 diferencias")
                return
            self.stdout.write("\n".join(lineas))
            raise CommandError(f"Verificación: {len(lineas)} diferencias")
        informe = migrar(datos, definitiva=opciones["definitiva"])
        self.stdout.write(informe.texto())
