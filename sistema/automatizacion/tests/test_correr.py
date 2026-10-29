from datetime import time
from unittest.mock import patch


from sistema.auditoria.models import Evento

from .. import tasks
from ..models import Regla
from ..servicios import correr_reglas
from . import LUNES, MARTES, ReglasTestCase, en


def eventos():
    return Evento.objects.filter(tipo="ejecucion_regla")


class CorrerReglasTest(ReglasTestCase):
    def test_regla_atrasada_corre_al_volver_el_mismo_dia(self):
        regla = self.regla(cuenta=self.rodrigo, nombre="Rodrigo primero")
        with patch("django.utils.timezone.now", return_value=en(LUNES, "08:20")):
            self.assertEqual(correr_reglas(en(LUNES, "08:20")), 1)
        self.assertEqual(self.lista()[0], "rodrigo.mp")
        regla.refresh_from_db()
        self.assertEqual(regla.ultima_ejecucion, en(LUNES, "08:20"))
        self.assertEqual(eventos().get().fecha, en(LUNES, "08:20"))

    def test_no_corre_dos_veces_el_mismo_dia(self):
        self.regla(cuenta=self.rodrigo)
        self.assertEqual(correr_reglas(en(LUNES, "08:20")), 1)
        self.assertEqual(correr_reglas(en(LUNES, "10:00")), 0)
        self.assertEqual(eventos().count(), 1)

    def test_regla_de_lunes_no_corre_el_martes(self):
        self.regla(cuenta=self.rodrigo)
        self.assertEqual(correr_reglas(en(MARTES, "09:00")), 0)
        self.assertEqual(eventos().count(), 0)

    def test_dos_reglas_a_la_misma_hora_corren_en_orden_de_creacion(self):
        self.regla(cuenta=self.tincho, nombre="Tincho primero")
        self.regla(cuenta=self.rodrigo, nombre="Rodrigo primero")
        self.assertEqual(correr_reglas(en(LUNES, "08:00")), 2)
        self.assertEqual(self.lista()[:2], ["rodrigo.mp", "alias.muestra"])

    def test_con_atraso_corre_primero_la_de_hora_menor(self):
        self.regla(cuenta=self.tincho, nombre="De las 08:10", hora=time(8, 10))
        self.regla(cuenta=self.rodrigo, nombre="De las 08:00", hora=time(8, 0))
        self.assertEqual(correr_reglas(en(LUNES, "08:20")), 2)
        nombres = [e.origen for e in eventos().order_by("id")]
        self.assertEqual(nombres, ["regla 'De las 08:00'", "regla 'De las 08:10'"])

    def test_una_regla_que_falla_no_frena_a_la_siguiente(self):
        primera = self.regla(cuenta=self.tincho, nombre="Falla", accion="ultimo")
        segunda = self.regla(cuenta=self.rodrigo, nombre="Sigue")
        with patch(
            "sistema.automatizacion.servicios._aplicar",
            side_effect=[RuntimeError(), ([], "sin cambios.")],
        ):
            with self.assertLogs("sistema.automatizacion.servicios", "ERROR") as logs:
                self.assertEqual(correr_reglas(en(LUNES, "08:00")), 1)
        self.assertIn("Regla no ejecutada", logs.output[0])
        primera.refresh_from_db()
        segunda.refresh_from_db()
        self.assertEqual(primera.ultima_ejecucion, en(LUNES, "08:00"))
        self.assertEqual(segunda.ultima_ejecucion, en(LUNES, "08:00"))

    def test_tarea_celery_corre_la_regla_con_el_reloj_fijo(self):
        self.regla(cuenta=self.rodrigo)
        with patch("django.utils.timezone.now", return_value=en(LUNES, "08:00")):
            self.assertEqual(tasks.correr_reglas.delay().get(), 1)
        self.assertEqual(self.lista()[0], "rodrigo.mp")

    def test_beat_schedule_apunta_a_la_tarea(self):
        from django.conf import settings

        item = settings.CELERY_BEAT_SCHEDULE["automatizacion-correr-reglas"]
        self.assertEqual(item["task"], "sistema.automatizacion.tasks.correr_reglas")

    def test_regla_inactiva_no_corre(self):
        self.regla(cuenta=self.rodrigo, estado=Regla.Estado.INACTIVA)
        self.assertEqual(correr_reglas(en(LUNES, "08:00")), 0)
