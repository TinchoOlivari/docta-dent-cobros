from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model

from sistema.asignacion.servicios import (
    SinCuentaDisponible,
    crear_cobro_pendiente,
    es_excedente,
    lista_con_cupo,
)
from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba, pago_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
OCT = date(2026, 10, 1)
EN_OCT = datetime(2026, 10, 20, 12, 0, tzinfo=ARG)
SEMANA = datetime(2026, 10, 21, 12, 0, tzinfo=ARG)


class CupoConPagosTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=EN_OCT)
        reloj.start()
        self.addCleanup(reloj.stop)
        self.admin = get_user_model().objects.create_user(
            "admin", email="a@example.com", role="administrator"
        )
        self.dest = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo", tipo="profesional"
        )
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="alias.uno",
            tope_mensual=D("500000.00"),
            tope_semanal=D("100000.00"),
        )

    def cobro(self, monto, estado="finalizado", fecha=EN_OCT):
        return cobro_prueba(
            destinatario=self.dest,
            cuenta=self.cuenta,
            monto=D(monto),
            estado=estado,
            fecha=fecha,
        )

    def pago(self, monto, **kw):
        kw.setdefault("cuenta", self.cuenta)
        kw.setdefault("creado_por", self.admin)
        return pago_prueba(destinatario=self.dest, monto=D(monto), **kw)

    def cupo(self, momento=EN_OCT):
        return lista_con_cupo(momento)[0].cupo_mensual

    def test_us2_2_pago_deja_el_cupo_en_cero_y_la_cuenta_no_se_elige(self):
        self.cobro("480000.00")
        self.pago("20000.00")
        self.assertEqual(self.cupo(), D("0.00"))
        with self.assertRaises(SinCuentaDisponible):
            crear_cobro_pendiente(monto=D("1.00"), usuario=self.admin)

    def test_bordes_del_pago_sobre_el_cupo(self):
        self.assertEqual(self.cupo(), D("500000.00"))
        self.cobro("480000.00")
        for monto, esperado in (("19999.99", "0.01"), ("20000.00", "0.00"), ("20000.01", "0.00")):
            with self.subTest(monto=monto):
                p = self.pago(monto)
                self.assertEqual(self.cupo(), D(esperado))
                type(p).objects.filter(pk=p.pk).update(estado="anulado", motivo_anulacion="no_pago")

    def test_sin_pago_el_cupo_es_el_tope_menos_cobros(self):
        self.cobro("480000.00")
        self.assertEqual(self.cupo(), D("20000.00"))

    def test_pago_anulado_y_efectivo_no_suman(self):
        self.pago("7000.00", estado="anulado")
        self.pago("8000.00", metodo="efectivo", cuenta=None)
        self.assertEqual(self.cupo(), D("500000.00"))

    def test_el_cupo_semanal_no_cambia_con_el_pago(self):
        antes = lista_con_cupo(SEMANA)[0].cupo_semanal
        self.pago("30000.00")
        self.assertEqual(lista_con_cupo(SEMANA)[0].cupo_semanal, antes)
        self.assertEqual(antes, D("100000.00"))

    def test_sin_join_doble_cada_monto_suma_una_vez(self):
        self.cobro("1000.00")
        self.cobro("2000.00")
        self.pago("500.00")
        self.assertEqual(self.cupo(), D("496500.00"))
        self.assertEqual(lista_con_cupo(EN_OCT)[0].recibido_mes, D("3500.00"))

    def test_pago_de_octubre_consume_octubre_y_no_noviembre(self):
        self.pago("50000.00")
        self.assertEqual(self.cupo(datetime(2026, 10, 20, tzinfo=ARG)), D("450000.00"))
        self.assertEqual(self.cupo(datetime(2026, 11, 3, tzinfo=ARG)), D("500000.00"))

    def test_excedente_justo_en_el_tope_y_un_centavo_arriba(self):
        self.pago("480000.00")
        for monto, esperado in (("20000.00", False), ("20000.01", True), ("19999.99", False)):
            with self.subTest(monto=monto):
                c = Cobro(cuenta=self.cuenta, monto=D(monto), estado="pendiente", fecha=EN_OCT)
                self.assertIs(es_excedente(c), esperado)

    def test_pago_anulado_no_cuenta_en_excedente(self):
        self.pago("480000.00", estado="anulado")
        c = Cobro(cuenta=self.cuenta, monto=D("500000.00"), estado="pendiente", fecha=EN_OCT)
        self.assertIs(es_excedente(c), False)

    def test_el_pago_que_pasa_el_tope_se_guarda_sin_marca(self):
        p = self.pago("600000.00")
        self.assertFalse(hasattr(p, "excedente"))
        self.assertEqual(self.cupo(), D("0.00"))
