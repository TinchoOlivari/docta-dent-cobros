from datetime import datetime
from decimal import Decimal

from sistema.asignacion.servicios import lista_con_cupo
from sistema.asignacion.tests.test_asignar import AHORA, ARG, DatosEjemplo
from sistema.cobros.models import Cobro
from sistema.destinatarios.models import Cuenta
from sistema.destinatarios.servicios import crear_destinatario, editar_cuenta

D = Decimal


class CupoTest(DatosEjemplo):
    def cuenta(self, alias, momento=None):
        return {c.alias: c for c in lista_con_cupo(momento)}[alias]

    def arranque(self):
        self.cobro(self.c1, "300000.00")
        return self.cobro(self.c1, "150000.00", estado="pendiente")

    def test_us2_1_pendiente_baja_el_cupo_y_justo_entra(self):
        self.arranque()
        self.assertEqual(self.cuenta("alias.uno").cupo_mensual, D("50000.00"))
        self.assertEqual(self.pedir("50000.00"), "alias.uno")

    def test_us2_1_un_centavo_arriba_pasa_a_la_siguiente(self):
        self.arranque()
        self.assertEqual(self.pedir("50000.01"), "jimena.roble.rana")

    def test_us2_2_cancelado_devuelve_el_cupo(self):
        self.arranque()
        Cobro.objects.filter(estado="pendiente").update(
            estado="cancelado", motivo_cancelacion="otro_medio"
        )
        self.assertEqual(self.cuenta("alias.uno").cupo_mensual, D("200000.00"))

    def test_us2_3_finalizado_no_cambia_el_cupo_y_mueve_la_columna(self):
        self.arranque()
        Cobro.objects.filter(estado="pendiente").update(
            estado="finalizado", comprobante="comprobantes/prueba.pdf"
        )
        c = self.cuenta("alias.uno")
        self.assertEqual(c.cupo_mensual, D("50000.00"))
        self.assertEqual(c.reservado_mes, D("0.00"))
        self.assertEqual(c.recibido_mes, D("450000.00"))

    def test_us2_4_pendiente_de_fin_de_mes_finalizado_cuenta_en_su_mes(self):
        fecha = datetime(2026, 10, 31, 23, 50, tzinfo=ARG)
        self.cobro(self.c1, "100000.00", estado="finalizado", fecha=fecha)
        nov = datetime(2026, 11, 1, 0, 10, tzinfo=ARG)
        self.assertEqual(self.cuenta("alias.uno", nov).recibido_mes, D("0.00"))
        self.assertEqual(
            self.cuenta("alias.uno", datetime(2026, 10, 31, tzinfo=ARG)).recibido_mes,
            D("100000.00"),
        )

    def test_fr008_limites_del_cupo_mensual(self):
        casos = [
            ("499999.99", "0.01"),
            ("500000.00", "0.00"),
            ("500000.01", "0.00"),
            ("0.00", "500000.00"),
        ]
        for total, cupo in casos:
            with self.subTest(total=total):
                Cobro.objects.update(estado="cancelado", motivo_cancelacion="otro_medio")
                if D(total):
                    self.cobro(self.c1, "100.00", estado="pendiente")
                    self.cobro(self.c1, str(D(total) - D("100.00")))
                self.assertEqual(self.cuenta("alias.uno").cupo_mensual, D(cupo))

    def test_fr009_limites_del_cupo_semanal(self):
        for total, cupo in [("99999.99", "0.01"), ("100000.00", "0.00"), ("100000.01", "0.00")]:
            with self.subTest(total=total):
                Cobro.objects.update(estado="cancelado", motivo_cancelacion="otro_medio")
                self.cobro(self.c2, total, fecha=AHORA)
                self.assertEqual(self.cuenta("jimena.roble.rana").cupo_semanal, D(cupo))

    def test_tope_semanal_cero_es_sin_limite(self):
        self.assertIsNone(self.cuenta("alias.uno").cupo_semanal)

    def test_domingo_ultimo_segundo_cuenta_en_semana_y_en_noviembre(self):
        domingo = datetime(2026, 11, 1, 23, 59, 59, tzinfo=ARG)
        self.cobro(self.c2, "1000.00", fecha=domingo)
        c = self.cuenta("jimena.roble.rana", domingo)
        self.assertEqual(c.recibido_semana, D("1000.00"))
        self.assertEqual(c.recibido_mes, D("1000.00"))

    def test_lunes_cero_horas_no_cuenta_en_la_semana_anterior(self):
        self.cobro(self.c2, "1000.00", fecha=datetime(2026, 11, 2, 0, 0, 0, tzinfo=ARG))
        self.assertEqual(self.cuenta("jimena.roble.rana").recibido_semana, D("0.00"))

    def test_cancelado_no_suma(self):
        self.cobro(self.c2, "1000.00", estado="cancelado", fecha=AHORA)
        c = self.cuenta("jimena.roble.rana")
        for campo in ("recibido_mes", "reservado_mes", "recibido_semana", "reservado_semana"):
            self.assertEqual(getattr(c, campo), D("0.00"), campo)

    def test_pendiente_de_septiembre_no_reserva_en_octubre(self):
        self.cobro(self.c1, "1000.00", "pendiente", datetime(2026, 9, 30, 23, 59, tzinfo=ARG))
        self.assertEqual(self.cuenta("alias.uno").cupo_mensual, D("500000.00"))

    def test_fr012_cambiar_destinatario_no_cambia_cupo_ni_posicion(self):
        self.cobro(self.c1, "300000.00")
        otro = crear_destinatario(usuario=None, nombre="Otro", tipo="profesional")
        editar_cuenta(self.c1, usuario=self.user, destinatario=otro)
        c = self.cuenta("alias.uno")
        self.assertEqual(c.cupo_mensual, D("200000.00"))
        self.assertEqual(c.posicion, 1)

    def test_fr013_subir_tope_mensual_actualiza_el_cupo_de_inmediato(self):
        self.cobro(self.c1, "500000.00")
        editar_cuenta(self.c1, usuario=self.user, tope_mensual=D("600000.00"))
        self.assertEqual(self.cuenta("alias.uno").cupo_mensual, D("100000.00"))

    def test_fr013_tope_semanal_cero_saca_el_limite(self):
        editar_cuenta(self.c2, usuario=self.user, tope_semanal=D("0.00"))
        self.assertIsNone(self.cuenta("jimena.roble.rana").cupo_semanal)


class CupoTopeDelPeriodoTest(DatosEjemplo):
    """FR-024, 001/FR-029: el cupo de un período cerrado usa el tope de ese período."""

    def cuenta(self, alias, momento):
        return {c.alias: c for c in lista_con_cupo(momento)}[alias]

    def setUp(self):
        super().setUp()
        self.sep = datetime(2026, 9, 15, 12, 0, tzinfo=ARG)
        self.c1.topes.update(desde=datetime(2026, 8, 1, tzinfo=ARG))
        editar_cuenta(self.c1, usuario=None, tope_mensual=D("400000.00"))  # rige desde AHORA

    def cupo_sep(self, recibido):
        self.cobro(self.c1, recibido, fecha=self.sep)
        return self.cuenta("alias.uno", self.sep).cupo_mensual

    def test_mes_cerrado_usa_el_tope_de_ese_mes_justo_en_el_tope(self):
        self.assertEqual(self.cupo_sep("450000.00"), D("50000.00"))

    def test_mes_cerrado_un_centavo_arriba_del_cupo(self):
        self.assertEqual(self.cupo_sep("450000.01"), D("49999.99"))

    def test_mes_cerrado_un_centavo_abajo_del_cupo(self):
        self.assertEqual(self.cupo_sep("449999.99"), D("50000.01"))

    def test_mes_cerrado_sin_recibido_es_el_tope_de_ese_mes(self):
        self.assertEqual(self.cuenta("alias.uno", self.sep).cupo_mensual, D("500000.00"))

    def test_mes_corriente_usa_el_tope_nuevo(self):
        self.assertEqual(self.cuenta("alias.uno", AHORA).cupo_mensual, D("400000.00"))

    def test_recibido_mayor_que_el_tope_nuevo_da_cupo_cero(self):
        self.cobro(self.c1, "450000.00", fecha=AHORA)
        self.assertEqual(self.cuenta("alias.uno", AHORA).cupo_mensual, D("0.00"))

    def test_lista_expone_el_tope_del_periodo(self):
        c = self.cuenta("alias.uno", self.sep)
        self.assertEqual(c.tope_mensual_periodo, D("500000.00"))
        self.assertEqual(self.cuenta("alias.uno", AHORA).tope_mensual_periodo, D("400000.00"))

    def test_prefetch_no_agrega_consultas_por_cuenta(self):
        with self.assertNumQueries(2):  # cuentas + topes
            lista_con_cupo(AHORA)


class CupoTopeSemanalDelPeriodoTest(DatosEjemplo):
    """Tope semanal $150.000 hasta el domingo, $100.000 desde el miércoles (AHORA)."""

    PASADA = datetime(2026, 10, 21, 12, 0, tzinfo=ARG)  # semana 19/10 al 25/10
    DOMINGO = datetime(2026, 10, 25, 23, 59, 59, tzinfo=ARG)
    LUNES = datetime(2026, 10, 26, 0, 0, tzinfo=ARG)

    def setUp(self):
        super().setUp()
        self.c2.topes.filter(tipo="semanal").update(
            monto=D("150000.00"), desde=datetime(2026, 8, 1, tzinfo=ARG)
        )
        Cuenta.objects.filter(pk=self.c2.pk).update(tope_semanal=D("150000.00"))
        self.c2.refresh_from_db()
        editar_cuenta(self.c2, usuario=None, tope_semanal=D("100000.00"))  # rige desde AHORA

    def semanal(self, momento):
        return {c.alias: c for c in lista_con_cupo(momento)}["jimena.roble.rana"].cupo_semanal

    def test_semana_pasada_usa_150000(self):
        self.cobro(self.c2, "120000.00", fecha=self.PASADA)
        self.assertEqual(self.semanal(self.PASADA), D("30000.00"))

    def test_semana_corriente_usa_100000(self):
        self.cobro(self.c2, "90000.00", fecha=datetime(2026, 10, 27, 9, 0, tzinfo=ARG))
        self.assertEqual(self.semanal(AHORA), D("10000.00"))

    def test_semana_corriente_justo_en_el_tope_nuevo_da_cero(self):
        self.cobro(self.c2, "100000.00", fecha=datetime(2026, 10, 27, 9, 0, tzinfo=ARG))
        self.assertEqual(self.semanal(AHORA), D("0.00"))

    def test_semana_pasada_un_centavo_arriba_del_tope_viejo_da_cero(self):
        self.cobro(self.c2, "150000.01", fecha=self.PASADA)
        self.assertEqual(self.semanal(self.PASADA), D("0.00"))

    def test_semana_pasada_un_centavo_abajo_del_tope_viejo(self):
        self.cobro(self.c2, "149999.99", fecha=self.PASADA)
        self.assertEqual(self.semanal(self.PASADA), D("0.01"))

    def test_justo_en_el_cierre_del_periodo_el_tope_nuevo_rige_desde_el_lunes(self):
        self.c2.topes.filter(tipo="semanal", monto=D("100000.00")).update(desde=self.LUNES)
        self.assertEqual(self.semanal(self.DOMINGO), D("150000.00"))
        self.assertEqual(self.semanal(self.LUNES), D("100000.00"))

    def test_tope_semanal_cero_del_periodo_es_sin_limite(self):
        self.c2.topes.filter(tipo="semanal", monto=D("150000.00")).update(monto=D("0.00"))
        self.assertIsNone(self.semanal(self.PASADA))
        self.assertEqual(self.semanal(AHORA), D("100000.00"))
