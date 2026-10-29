from datetime import date, datetime
from decimal import Decimal

from sistema.asignacion.servicios import lista_con_cupo, semanas_del_mes
from sistema.cobros.models import PagoComplementario
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal


def en(dia, mes=10, hora=12, minuto=0, segundo=0, anio=2026):
    return datetime(anio, mes, dia, hora, minuto, segundo, tzinfo=ARG)


class SemanasTest(SistemaTestCase):
    def setUp(self):
        self.d = crear_destinatario(usuario=None, nombre="Dueño Semanas", tipo="profesional")
        self.c = self.cuenta("200000.00")

    def cuenta(self, semanal, alias="semanas.uno"):
        return crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=self.d, alias=alias,
            tope_mensual=D("9000000.00"), tope_semanal=D(semanal),
        )  # fmt: skip

    def cobro(self, monto, fecha, estado="finalizado"):
        cobro_prueba(cuenta=self.c, monto=D(monto), estado=estado, fecha=fecha)

    def semanas(self, mes=date(2026, 10, 1)):
        return semanas_del_mes(self.c, mes)

    def test_octubre_2026_tiene_5_semanas_con_los_rangos_de_la_spec(self):
        rangos = [(s["inicio"], s["fin"]) for s in self.semanas()]
        self.assertEqual(
            rangos,
            [
                (date(2026, 9, 28), date(2026, 10, 4)),
                (date(2026, 10, 5), date(2026, 10, 11)),
                (date(2026, 10, 12), date(2026, 10, 18)),
                (date(2026, 10, 19), date(2026, 10, 25)),
                (date(2026, 10, 26), date(2026, 11, 1)),
            ],
        )

    def test_semana_a_caballo_entre_meses_cuenta_los_dos_lados(self):
        self.cobro("50000.00", en(30, 9))
        self.cobro("100000.00", en(4, hora=23, minuto=59, segundo=59))
        s = self.semanas()[0]
        self.assertEqual((s["recibido"], s["cupo"]), (D("150000.00"), D("50000.00")))

    def test_cobro_del_lunes_a_las_0_va_a_la_semana_2(self):
        self.cobro("10000.00", en(5, hora=0))
        s = self.semanas()
        self.assertEqual((s[0]["recibido"], s[1]["recibido"]), (D("0.00"), D("10000.00")))

    def test_pendiente_del_1_de_noviembre_es_reservado_de_la_semana_5(self):
        self.cobro("200000.00", en(1, 11), estado="pendiente")
        s = self.semanas()[4]
        self.assertEqual((s["reservado"], s["recibido"], s["cupo"]), (D("200000.00"), 0, 0))

    def test_cancelado_y_pago_complementario_no_cuentan(self):
        self.cobro("100000.00", en(7), estado="cancelado")
        PagoComplementario.objects.create(
            destinatario=self.d, mes=date(2026, 10, 1), monto=D("90000.00"),
            metodo="transferencia", cuenta=self.c, creado_por=self._admin(),
        )  # fmt: skip
        s = self.semanas()[1]
        self.assertEqual((s["recibido"], s["reservado"], s["cupo"]), (0, 0, D("200000.00")))

    def _admin(self):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create_user(
            "adm017s", email="adm017s@example.com", role="administrator"
        )

    def test_casos_limite_del_cupo_semanal(self):
        for i, (recibido, cupo) in enumerate(
            [
                ("199999.99", "0.01"),
                ("200000.00", "0.00"),
                ("200000.01", "0.00"),
                ("0.00", "200000.00"),
            ]
        ):
            with self.subTest(recibido=recibido):
                self.c = self.cuenta("200000.00", alias=f"semanas.limite{i}")
                if D(recibido):
                    self.cobro(recibido, en(14))
                self.assertEqual(self.semanas()[2]["cupo"], D(cupo))

    def test_tope_cero_da_cupo_none(self):
        self.c = self.cuenta("0.00", alias="semanas.dos")
        self.assertTrue(all(s["cupo"] is None for s in self.semanas()))

    def test_febrero_2027_tiene_4_semanas(self):
        self.assertEqual(len(self.semanas(date(2027, 2, 1))), 4)

    def test_lista_con_cupo_sin_argumentos_sigue_siendo_la_lista_de_prioridad(self):
        otra = self.cuenta("0.00", alias="semanas.tres")
        self.assertEqual([c.pk for c in lista_con_cupo()], [self.c.pk, otra.pk])
