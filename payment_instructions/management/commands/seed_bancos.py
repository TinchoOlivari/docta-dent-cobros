import json
import logging
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from payment_instructions.models import Banco

logger = logging.getLogger(__name__)

BANCOS_JSON_PATH = Path(settings.BASE_DIR) / "payment_instructions" / "bancos.json"


class Command(BaseCommand):
    help = "Crea o actualiza los bancos de payment_instructions/bancos.json y descarga sus logos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Vuelve a descargar el logo aunque el banco ya tenga uno cargado.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        data = json.loads(BANCOS_JSON_PATH.read_text(encoding="utf-8"))

        creados = 0
        actualizados = 0
        saltados = 0
        fallidos = 0

        for item in data["items"]:
            nombre = item["name"]
            banco, created = Banco.objects.get_or_create(nombre=nombre)

            if banco.logo and not force:
                saltados += 1
                continue

            try:
                self._descargar_logo(banco, item["google_domain"])
            except Exception:
                logger.exception("No se pudo descargar el logo de %s", nombre)
                self.stderr.write(self.style.WARNING(f"Falló la descarga del logo: {nombre}"))
                fallidos += 1
                continue

            if created:
                creados += 1
            else:
                actualizados += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Bancos creados: {creados}, actualizados: {actualizados}, "
                f"saltados (ya tenían logo): {saltados}, fallidos: {fallidos}"
            )
        )

    def _descargar_logo(self, banco, url):
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            contenido = response.read()

        nombre_archivo = f"{banco.nombre}.png"
        banco.logo.save(nombre_archivo, ContentFile(contenido), save=True)
