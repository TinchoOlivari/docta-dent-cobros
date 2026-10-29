from datetime import date, datetime, timezone

from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.reportes.servicios import leer_mes, mes_corriente

AHORA = datetime(2026, 11, 5, 10, 0, tzinfo=ARG)


class MesTest(SistemaTestCase):
    def test_mes_corriente(self):
        self.assertEqual(mes_corriente(AHORA), date(2026, 11, 1))

    def test_mes_corriente_en_hora_argentina_no_utc(self):
        # 1/11 02:59 UTC es 31/10 23:59 en Argentina
        ahora = datetime(2026, 11, 1, 2, 59, tzinfo=timezone.utc)
        self.assertEqual(mes_corriente(ahora), date(2026, 10, 1))

    def test_leer_mes_valido(self):
        self.assertEqual(leer_mes("2026-10", AHORA), date(2026, 10, 1))

    def test_leer_mes_del_mes_corriente(self):
        self.assertEqual(leer_mes("2026-11", AHORA), date(2026, 11, 1))

    def test_leer_mes_futuro_invalido_o_vacio_da_el_corriente(self):
        for texto in ("2026-12", "basura", None, ""):
            with self.subTest(texto=texto):
                self.assertEqual(leer_mes(texto, AHORA), date(2026, 11, 1))
