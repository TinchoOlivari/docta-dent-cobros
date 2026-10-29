from datetime import datetime, timezone

from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG, rango_mes_arg, rango_semana_arg


def arg(*a):
    return datetime(*a, tzinfo=ARG)


class RangoMesTest(SistemaTestCase):
    def test_ultimo_segundo_de_octubre_es_octubre(self):
        self.assertEqual(
            rango_mes_arg(arg(2026, 10, 31, 23, 59, 59)), (arg(2026, 10, 1), arg(2026, 11, 1))
        )

    def test_primer_segundo_de_noviembre_es_noviembre(self):
        self.assertEqual(
            rango_mes_arg(arg(2026, 11, 1, 0, 0, 0)), (arg(2026, 11, 1), arg(2026, 12, 1))
        )

    def test_diciembre_termina_el_1_de_enero(self):
        self.assertEqual(rango_mes_arg(arg(2026, 12, 31, 12)), (arg(2026, 12, 1), arg(2027, 1, 1)))

    def test_utc_de_la_madrugada_cuenta_en_hora_argentina(self):
        # 31/10 02:59:59 UTC = 30/10 23:59:59 ARG
        momento = datetime(2026, 10, 31, 2, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(rango_mes_arg(momento)[0], arg(2026, 10, 1))
        # 1 segundo después: 31/10 00:00:00 ARG, sigue octubre
        # 1/11 03:00:00 UTC = 1/11 00:00:00 ARG
        momento = datetime(2026, 11, 1, 3, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(rango_mes_arg(momento)[0], arg(2026, 11, 1))


class RangoSemanaTest(SistemaTestCase):
    def test_domingo_ultimo_segundo_es_la_semana_del_lunes_anterior(self):
        # Semana que cruza dos meses: lunes 26/10 a domingo 1/11
        self.assertEqual(
            rango_semana_arg(arg(2026, 11, 1, 23, 59, 59)), (arg(2026, 10, 26), arg(2026, 11, 2))
        )

    def test_lunes_cero_horas_empieza_semana_nueva(self):
        self.assertEqual(
            rango_semana_arg(arg(2026, 11, 2, 0, 0, 0)), (arg(2026, 11, 2), arg(2026, 11, 9))
        )
