from datetime import date, datetime, time, timedelta, timezone

from ..servicios import toca
from . import LUNES, MARTES, ReglasTestCase, en

UTC = timezone.utc
LUNES_SIGUIENTE = LUNES + timedelta(days=7)


class TocaSemanaTest(ReglasTestCase):
    def test_toca_justo_a_la_hora_y_no_un_segundo_antes(self):
        regla = self.regla()  # lunes 08:00, creada 07:00
        self.assertFalse(toca(regla, en(LUNES, "08:00") - timedelta(seconds=1)))
        self.assertTrue(toca(regla, en(LUNES, "08:00")))

    def test_no_toca_otro_dia_de_la_semana(self):
        self.assertFalse(toca(self.regla(), en(MARTES, "08:00")))

    def test_toca_lunes_2359_arg_aunque_en_utc_sea_martes(self):
        regla = self.regla(hora=time(23, 59))
        self.assertTrue(toca(regla, datetime(2026, 10, 20, 2, 59, tzinfo=UTC)))

    def test_no_toca_martes_0000_arg_cruce_de_utc(self):
        regla = self.regla(hora=time(23, 59))
        self.assertFalse(toca(regla, datetime(2026, 10, 20, 3, 0, tzinfo=UTC)))

    def test_no_toca_si_esta_inactiva(self):
        regla = self.regla(estado="inactiva")
        self.assertFalse(toca(regla, en(LUNES, "08:00")))


class TocaMesTest(ReglasTestCase):
    def toca_el(self, dia_regla, *fechas):
        regla = self.regla(cuando=en(date(2026, 1, 1), "07:00"), disparador="mes", dia=dia_regla)
        return [toca(regla, en(f, "08:00")) for f in fechas]

    def test_dia_31_corre_el_30_de_noviembre_y_no_el_29(self):
        self.assertEqual(self.toca_el(31, date(2026, 11, 30), date(2026, 11, 29)), [True, False])

    def test_dia_30_corre_el_28_de_febrero_2027(self):
        self.assertEqual(self.toca_el(30, date(2027, 2, 28)), [True])

    def test_dia_29_corre_el_29_de_febrero_bisiesto_y_no_el_28(self):
        self.assertEqual(self.toca_el(29, date(2028, 2, 29), date(2028, 2, 28)), [True, False])

    def test_dia_31_en_octubre_corre_el_31_y_no_el_30(self):
        self.assertEqual(self.toca_el(31, date(2026, 10, 31), date(2026, 10, 30)), [True, False])

    def test_dia_1_corre_el_1_de_noviembre(self):
        self.assertEqual(self.toca_el(1, date(2026, 11, 1)), [True])

    def test_dia_28_corre_el_28_de_febrero_2027(self):
        self.assertEqual(self.toca_el(28, date(2027, 2, 28)), [True])

    def test_dia_31_no_corre_el_dia_1_del_mes_siguiente(self):
        self.assertEqual(self.toca_el(31, date(2026, 12, 1)), [False])


class TocaUnaVezPorDiaTest(ReglasTestCase):
    def test_con_ultima_ejecucion_el_mismo_dia_no_toca_y_la_semana_siguiente_si(self):
        regla = self.regla()
        regla.ultima_ejecucion = en(LUNES, "08:00")
        self.assertFalse(toca(regla, en(LUNES, "10:00")))
        self.assertTrue(toca(regla, en(LUNES_SIGUIENTE, "08:00")))

    def test_ultima_ejecucion_de_ayer_2359_no_bloquea_hoy(self):
        regla = self.regla(disparador="mes", dia=20)
        regla.ultima_ejecucion = en(LUNES, "23:59")
        self.assertTrue(toca(regla, en(MARTES, "08:00")))

    def test_ultima_ejecucion_hoy_0000_bloquea_hoy(self):
        regla = self.regla()
        regla.ultima_ejecucion = en(LUNES, "00:00")
        self.assertFalse(toca(regla, en(LUNES, "08:00")))


class TocaFechaCambioTest(ReglasTestCase):
    def test_creada_despues_de_su_hora_no_corre_ese_dia_y_corre_la_semana_siguiente(self):
        regla = self.regla(cuando=en(LUNES, "10:00"))
        self.assertFalse(toca(regla, en(LUNES, "10:05")))
        self.assertTrue(toca(regla, en(LUNES_SIGUIENTE, "08:00")))

    def test_editada_despues_de_su_hora_no_corre_ese_dia(self):
        regla = self.regla()
        regla.fecha_cambio = en(LUNES, "08:30")
        self.assertFalse(toca(regla, en(LUNES, "09:00")))

    def test_activada_despues_de_su_hora_no_corre_ese_dia(self):
        regla = self.regla(cuando=en(LUNES, "09:00"))
        self.assertFalse(toca(regla, en(LUNES, "09:30")))

    def test_creada_justo_a_la_hora_corre_ese_dia(self):
        regla = self.regla(cuando=en(LUNES, "08:00"))
        self.assertTrue(toca(regla, en(LUNES, "08:00")))

    def test_creada_un_minuto_despues_de_la_hora_no_corre(self):
        regla = self.regla(cuando=en(LUNES, "08:01"))
        self.assertFalse(toca(regla, en(LUNES, "08:01")))
