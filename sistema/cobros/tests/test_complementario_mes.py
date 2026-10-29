from datetime import date, datetime, timezone as utc
from unittest import mock

from sistema.cobros import servicios
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG


class MesPropuestoTest(SistemaTestCase):
    def test_dia_10_a_las_23_59_59_propone_mes_anterior(self):
        ahora = datetime(2026, 11, 10, 23, 59, 59, tzinfo=ARG)
        self.assertEqual(servicios.mes_propuesto(ahora), date(2026, 10, 1))

    def test_dia_11_a_las_00_00_propone_mes_corriente(self):
        ahora = datetime(2026, 11, 11, 0, 0, tzinfo=ARG)
        self.assertEqual(servicios.mes_propuesto(ahora), date(2026, 11, 1))

    def test_dia_1_propone_mes_anterior(self):
        ahora = datetime(2026, 11, 1, 0, 0, tzinfo=ARG)
        self.assertEqual(servicios.mes_propuesto(ahora), date(2026, 10, 1))

    def test_usa_hora_argentina_no_utc(self):
        # 02:59 UTC del 11/11 es 23:59 del 10/11 en Argentina
        ahora = datetime(2026, 11, 11, 2, 59, tzinfo=utc.utc)
        self.assertEqual(servicios.mes_propuesto(ahora), date(2026, 10, 1))

    def test_enero_propone_diciembre_del_anio_anterior(self):
        ahora = datetime(2027, 1, 5, 12, 0, tzinfo=ARG)
        self.assertEqual(servicios.mes_propuesto(ahora), date(2026, 12, 1))

    def test_sin_argumento_usa_timezone_now(self):
        ahora = datetime(2026, 11, 20, 9, 0, tzinfo=ARG)
        with mock.patch("django.utils.timezone.now", return_value=ahora):
            self.assertEqual(servicios.mes_propuesto(), date(2026, 11, 1))
