import importlib

from django.apps import apps
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from sistema.core.tests import SistemaTestCase

migracion = importlib.import_module("sistema.automatizacion.migrations.0002_apagar_regla_vieja")


class ReglaViejaTests(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.cada_minuto = IntervalSchedule.objects.create(every=1, period="minutes")

    def _tarea(self, nombre, task):
        return PeriodicTask.objects.create(
            name=nombre, task=task, interval=self.cada_minuto, enabled=True
        )

    def test_apagar_desactiva_la_regla_vieja_y_no_la_nueva(self):
        vieja = self._tarea(
            "Execute automation rules", "payment_instructions.tasks.execute_automation_rules"
        )
        nueva = self._tarea("Reglas nuevas", "sistema.automatizacion.tasks.correr_reglas")
        migracion.apagar(apps, None)
        vieja.refresh_from_db()
        nueva.refresh_from_db()
        self.assertFalse(vieja.enabled)
        self.assertTrue(nueva.enabled)

    def test_apagar_sin_la_regla_vieja_no_falla(self):
        migracion.apagar(apps, None)
        self.assertEqual(PeriodicTask.objects.count(), 0)
