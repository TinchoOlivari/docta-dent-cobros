import json
import shutil
import tempfile
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError

from sistema.cobros.models import Cobro
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.migracion.tests import fabricas as f

AHORA = datetime(2026, 11, 20, 12, 0, tzinfo=ARG)


class ComandoBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        f.usuario("completar", role="professional")
        self.ana = f.usuario("ana")
        self.perez = f.usuario("drperez", role="professional")
        self.fila = f.fila("Tincho", alias="alias.uno")
        carpeta = tempfile.mkdtemp(prefix="test-datos-migracion-")
        self.addCleanup(shutil.rmtree, carpeta, ignore_errors=True)
        self.ruta = Path(carpeta) / "datos.json"
        self.escribir({"para_completar_pagos": "completar", "tipos": {}, "vinculos": {}})

    def escribir(self, datos):
        self.ruta.write_text(json.dumps(datos), encoding="utf-8")

    def correr(self, **opciones):
        salida = StringIO()
        with patch("django.utils.timezone.now", return_value=AHORA):
            call_command("migrar_prod", datos=str(self.ruta), stdout=salida, **opciones)
        return salida.getvalue()

    def conflicto(self):
        # Mismo alias en dos filas: sin restricción única porque la fábrica salta full_clean.
        f.fila("Otro", alias="ALIAS.uno", priority_order=2)


class ComandoTest(ComandoBase):
    def test_por_defecto_es_revision_y_no_deja_cobros(self):
        f.pago(self.fila, 1000, self.ana, self.perez)
        salida = self.correr()
        self.assertIn("Modo: revisión", salida)
        self.assertIn("Verificación: 0 diferencias", salida)
        self.assertFalse(Cobro.objects.exists())

    def test_definitiva_deja_los_cobros(self):
        f.pago(self.fila, 1000, self.ana, self.perez)
        salida = self.correr(definitiva=True)
        self.assertIn("Modo: definitiva", salida)
        self.assertIn("Verificación: 0 diferencias", salida)
        self.assertEqual(Cobro.objects.count(), 1)

    def test_definitiva_con_conflicto_falla_y_no_escribe(self):
        self.conflicto()
        with self.assertRaises(CommandError):
            self.correr(definitiva=True)
        self.assertFalse(Cobro.objects.exists())

    def test_datos_que_no_existe_falla(self):
        self.ruta.unlink()
        with self.assertRaises(CommandError):
            self.correr()

    def test_datos_es_obligatorio(self):
        with self.assertRaises(CommandError):
            call_command("migrar_prod", stdout=StringIO())

    def test_informe_de_revision_con_conflicto(self):
        self.conflicto()
        salida = self.correr()
        self.assertIn("Conflictos: 1", salida)
        self.assertIn("alias.uno", salida.lower())
        self.assertIn("Especialistas sin vínculo a Dentalink: 1", salida)
        self.assertIn("drperez", salida)
        self.assertIn("Verificación: No corre: hay conflictos o errores", salida)
