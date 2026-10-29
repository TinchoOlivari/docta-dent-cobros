from datetime import datetime
from decimal import Decimal

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.asignacion.servicios import crear_cobro_pendiente, es_excedente, lista_con_cupo
from sistema.asignacion.tests.test_asignar import AHORA, ARG, DatosEjemplo
from sistema.cobros.models import Cobro
from sistema.destinatarios.models import Cuenta
from sistema.destinatarios.servicios import editar_cuenta

D = Decimal


class ExcedenteTest(DatosEjemplo):
    def nuevo(self, monto, cuenta=None, **kw):
        kw.setdefault("estado", "finalizado")
        kw.setdefault("fecha", AHORA)
        return Cobro(cuenta=cuenta or self.c1, monto=D(monto), **kw)

    def test_us5_1_justo_en_el_tope_no_marca(self):
        self.cobro(self.c1, "450000.00")
        self.assertFalse(es_excedente(self.nuevo("50000.00")))

    def test_us5_2_un_centavo_arriba_marca(self):
        self.cobro(self.c1, "450000.00")
        self.assertTrue(es_excedente(self.nuevo("50000.01")))

    def test_un_centavo_abajo_no_marca(self):
        self.cobro(self.c1, "450000.00")
        self.assertFalse(es_excedente(self.nuevo("49999.99")))

    def test_recibido_cero_y_monto_igual_al_tope_no_marca(self):
        self.assertFalse(es_excedente(self.nuevo("500000.00")))

    def test_pendiente_cuenta_como_reservado(self):
        self.cobro(self.c1, "450000.00", estado="pendiente")
        self.assertTrue(es_excedente(self.nuevo("50000.01")))

    def test_el_cobro_no_se_cuenta_dos_veces(self):
        self.cobro(self.c1, "450000.00")
        cobro = cobro_prueba(cuenta=self.c1, monto=D("50000.01"), estado="finalizado", fecha=AHORA)
        self.assertTrue(es_excedente(cobro))
        cobro.monto = D("50000.00")
        self.assertFalse(es_excedente(cobro))

    def test_cambiar_fecha_al_mes_anterior_sin_cobros_no_marca(self):
        self.cobro(self.c1, "450000.00")
        cobro = self.nuevo("50000.01")
        self.assertTrue(es_excedente(cobro))
        cobro.fecha = datetime(2026, 9, 30, 23, 59, 59, tzinfo=ARG)
        self.assertFalse(es_excedente(cobro))

    def test_cambiar_cuenta_evalua_contra_su_tope(self):
        self.cobro(self.c1, "450000.00")
        self.cobro(self.c2, "290000.00")
        cobro = self.nuevo("50000.01")
        self.assertTrue(es_excedente(cobro))
        cobro.cuenta = self.c2
        self.assertTrue(es_excedente(cobro))  # 290000.00 + 50000.01 > 300000.00
        cobro.monto = D("10000.00")
        self.assertFalse(es_excedente(cobro))  # justo en el tope

    def test_cuenta_de_efectivo_no_marca(self):
        efectivo = Cuenta(tipo="efectivo", tope_mensual=D("0.00"))
        self.assertFalse(es_excedente(self.nuevo("999999.00", cuenta=efectivo)))

    def test_cancelado_no_marca(self):
        self.cobro(self.c1, "500000.00")
        self.assertFalse(es_excedente(self.nuevo("1.00", estado="cancelado")))

    def test_pasar_el_tope_semanal_sin_pasar_el_mensual_no_marca(self):
        self.cobro(self.c2, "90000.00", fecha=AHORA)
        self.assertFalse(es_excedente(self.nuevo("20000.00", cuenta=self.c2)))


class CambioDeTopeTest(DatosEjemplo):
    def test_us5_3_bajar_el_tope_no_toca_cobros_y_el_cupo_es_cero(self):
        self.cobro(self.c1, "500000.00")
        antes = list(Cobro.objects.values_list("pk", "excedente", "fecha_cambio"))
        editar_cuenta(self.c1, usuario=self.user, tope_mensual=D("400000.00"))
        self.assertEqual(list(Cobro.objects.values_list("pk", "excedente", "fecha_cambio")), antes)
        cuentas = {c.alias: c for c in lista_con_cupo()}
        self.assertEqual(cuentas["alias.uno"].cupo_mensual, D("0.00"))
        self.assertEqual(self.pedir("10000.00"), "jimena.roble.rana")

    def test_fr022_un_cobro_excedente_sigue_marcado_al_subir_el_tope(self):
        cobro = cobro_prueba(
            cuenta=self.c1, monto=D("600000.00"), estado="finalizado", fecha=AHORA, excedente=True
        )
        editar_cuenta(self.c1, usuario=self.user, tope_mensual=D("900000.00"))
        cobro.refresh_from_db()
        self.assertTrue(cobro.excedente)

    def test_crear_cobro_pendiente_nunca_deja_excedente(self):
        self.cobro(self.c1, "450000.00")
        for monto in ("50000.00", "40000.00", "10000.00"):
            self.assertFalse(crear_cobro_pendiente(monto=D(monto), usuario=self.user).excedente)
        self.assertFalse(Cobro.objects.filter(excedente=True).exists())


class ExcedenteTopeDelMesTest(DatosEjemplo):
    """FR-030: un cobro de un mes cerrado se compara con el tope de ese mes."""

    def setUp(self):
        super().setUp()
        self.c1.topes.update(desde=datetime(2026, 8, 1, tzinfo=ARG))
        editar_cuenta(self.c1, usuario=None, tope_mensual=D("400000.00"))  # rige desde AHORA
        self.sep = datetime(2026, 9, 15, 12, 0, tzinfo=ARG)
        self.cobro(self.c1, "450000.00", fecha=self.sep)

    def nuevo(self, monto, fecha):
        return Cobro(cuenta=self.c1, monto=D(monto), estado="finalizado", fecha=fecha)

    def test_mes_cerrado_justo_en_el_tope_de_ese_mes_no_marca(self):
        self.assertFalse(es_excedente(self.nuevo("50000.00", self.sep)))

    def test_mes_cerrado_un_centavo_arriba_del_tope_de_ese_mes_marca(self):
        self.assertTrue(es_excedente(self.nuevo("50000.01", self.sep)))

    def test_mes_cerrado_un_centavo_abajo_no_marca(self):
        self.assertFalse(es_excedente(self.nuevo("49999.99", self.sep)))

    def test_mes_corriente_usa_el_tope_nuevo(self):
        self.cobro(self.c1, "350000.00", fecha=AHORA)
        self.assertFalse(es_excedente(self.nuevo("50000.00", AHORA)))
        self.assertTrue(es_excedente(self.nuevo("50000.01", AHORA)))
