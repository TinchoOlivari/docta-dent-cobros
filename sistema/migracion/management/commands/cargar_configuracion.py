from django.core.management.base import BaseCommand

from sistema.migracion.servicios import cargar_configuracion, leer_configuracion


class Command(BaseCommand):
    help = "Carga la configuración inicial del sistema nuevo (una sola vez, todo o nada)."

    def add_arguments(self, parser):
        parser.add_argument("--datos", required=True, help="Ruta de configuracion.json")

    def handle(self, *args, **opciones):
        n = cargar_configuracion(leer_configuracion(opciones["datos"]), stdout=self.stdout)

        def cant(k, uno, varios):
            return f"{n[k]} {uno if n[k] == 1 else varios}"

        self.stdout.write(
            "Configuración inicial cargada: 1 cuenta de efectivo, "
            f"{cant('terminales', 'terminal', 'terminales')}, "
            f"{cant('cuentas_pos', 'cuenta de terminal POS', 'cuentas de terminal POS')}, "
            f"{cant('sesiones', 'sesión', 'sesiones')}, "
            f"{cant('direcciones', 'dirección de aviso', 'direcciones de aviso')}."
        )
