from datetime import date
from unittest.mock import patch

from sistema.auditoria.models import Evento

from ..servicios import correr_reglas
from . import LUNES, ReglasTestCase, en

LUNES_2 = date(2026, 10, 26)
LUNES_3 = date(2026, 11, 2)


def eventos():
    return Evento.objects.filter(tipo="ejecucion_regla")


class FalloDeReglaTest(ReglasTestCase):
    def test_error_inesperado_se_registra_y_la_regla_no_reintenta_el_mismo_dia(self):
        regla = self.regla(cuenta=self.rodrigo, cuando=en(LUNES, "07:00"))
        with patch("sistema.automatizacion.servicios._aplicar", side_effect=RuntimeError("x")):
            with self.assertLogs("sistema.automatizacion.servicios", "ERROR"):
                self.assertEqual(correr_reglas(en(LUNES_2, "08:00")), 0)
            self.assertEqual(correr_reglas(en(LUNES_2, "08:01")), 0)
        e = eventos().get()
        self.assertFalse(e.exito)
        self.assertIsNone(e.usuario)
        self.assertEqual(e.detalle, "Error inesperado (RuntimeError).")
        regla.refresh_from_db()
        self.assertEqual(regla.ultima_ejecucion, en(LUNES_2, "08:00"))

    def test_corre_de_nuevo_el_lunes_siguiente(self):
        self.regla(cuenta=self.rodrigo)
        with patch("sistema.automatizacion.servicios._aplicar", side_effect=RuntimeError()):
            with self.assertLogs("sistema.automatizacion.servicios", "ERROR"):
                correr_reglas(en(LUNES_2, "08:00"))
        self.assertEqual(correr_reglas(en(LUNES_3, "08:00")), 1)

    def test_otra_regla_del_mismo_minuto_corre(self):
        self.regla(cuenta=self.tincho, nombre="Falla", accion="ultimo")
        self.regla(cuenta=self.rodrigo, nombre="Sigue")
        with patch(
            "sistema.automatizacion.servicios._aplicar",
            side_effect=[RuntimeError(), ([], "sin cambios.")],
        ):
            with self.assertLogs("sistema.automatizacion.servicios", "ERROR"):
                self.assertEqual(correr_reglas(en(LUNES_2, "08:00")), 1)
        self.assertEqual(eventos().count(), 2)

    def test_si_registrar_tambien_falla_se_loguea_y_no_lanza(self):
        self.regla(cuenta=self.rodrigo)
        with (
            patch("sistema.automatizacion.servicios._aplicar", side_effect=RuntimeError()),
            patch("sistema.automatizacion.servicios._registrar_fallo", side_effect=OSError()),
        ):
            with self.assertLogs("sistema.automatizacion.servicios", "ERROR") as logs:
                self.assertEqual(correr_reglas(en(LUNES_2, "08:00")), 0)
        self.assertTrue(any("Fallo de regla no registrado" in o for o in logs.output))
