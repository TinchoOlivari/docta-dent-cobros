from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.auditoria.servicios import historial_de
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta, Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta, editar_cuenta
from sistema.terminales.models import Terminal
from sistema.terminales.servicios import (
    acumulado,
    avisar_objetivo,
    cambiar_acumula_desde,
    cambiar_cuenta,
    corregir_cambio,
    cruza_objetivo,
)
from sistema.terminales.tests.test_sesion import SesionBase, h

D = Decimal
AHORA = datetime(2026, 10, 14, 10, 0, tzinfo=ARG)


class AcumuladoBaseTest(SistemaTestCase):
    def setUp(self):
        self.prov = Proveedor.objects.get(nombre="Mercadopago")
        self.d = Destinatario.objects.create(nombre="Rodrigo", tipo="clinica")
        with mock.patch("django.utils.timezone.now", return_value=AHORA):
            self.cuenta = self.pos()
        self.t1 = Terminal.objects.create(nombre="T1", proveedor=self.prov, acepta_debito=True)
        self.t2 = Terminal.objects.create(nombre="T2", proveedor=self.prov, acepta_debito=True)

    def pos(self):
        return crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=self.d,
            titular="Titular Test",
            proveedor=self.prov,
            objetivo=D("300000.00"),
        )

    def cobro(self, monto, terminal=None, fecha=None, estado="finalizado"):
        return cobro_prueba(
            cuenta=self.cuenta,
            terminal=terminal or self.t1,
            destinatario=self.d,
            monto=D(monto),
            estado=estado,
            metodo="debito",
            fecha=fecha or AHORA,
        )

    def test_sin_cobros_es_cero(self):
        self.assertEqual(acumulado(self.cuenta), D("0.00"))

    def test_suma_dos_terminales(self):
        self.cobro("100000.00", self.t1)
        self.cobro("100000.00", self.t2)
        self.assertEqual(acumulado(self.cuenta), D("200000.00"))

    def test_cobro_cancelado_no_suma(self):
        self.cobro("260000.00")
        c = self.cobro("50000.00")
        self.assertEqual(acumulado(self.cuenta), D("310000.00"))
        c.estado = "cancelado"
        c.motivo_cancelacion = "otro_medio"
        c.save()
        self.assertEqual(acumulado(self.cuenta), D("260000.00"))

    def test_un_segundo_antes_de_acumula_desde_no_suma_y_justo_en_el_limite_si(self):
        self.cobro("10.00", fecha=AHORA - timedelta(seconds=1))
        self.assertEqual(acumulado(self.cuenta), D("0.00"))
        self.cobro("10.00", fecha=AHORA)
        self.assertEqual(acumulado(self.cuenta), D("10.00"))
        self.cobro("0.01", fecha=AHORA + timedelta(seconds=1))
        self.assertEqual(acumulado(self.cuenta), D("10.01"))

    def test_crear_cuenta_pos_acumula_desde_es_ahora(self):
        self.assertEqual(self.cuenta.acumula_desde, AHORA)

    def test_cuenta_transferencia_sin_acumula_desde(self):
        c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.d,
            titular="Titular Test",
            alias="alias.test.uno",
            tope_mensual=D("1000.00"),
        )
        self.assertIsNone(c.acumula_desde)

    def test_editar_cuenta_rechaza_acumula_desde(self):
        with self.assertRaises(ValidationError):
            editar_cuenta(self.cuenta, usuario=None, acumula_desde=AHORA)


class CruzaObjetivoTest(AcumuladoBaseTest):
    def test_bordes(self):
        o = D("300000.00")
        casos = [
            ("299999.99", "300000.00", True),
            ("250000.00", "299999.99", False),
            ("300000.00", "320000.00", False),
            ("299999.99", "300000.01", True),
            ("0.00", "0.00", False),
        ]
        for antes, despues, esperado in casos:
            with self.subTest(antes=antes, despues=despues):
                self.assertIs(cruza_objetivo(D(antes), D(despues), o), esperado)

    def test_objetivo_bajado_no_cruza(self):
        self.assertFalse(cruza_objetivo(D("250000.00"), D("270000.00"), D("200000.00")))

    def test_avisar_objetivo_loguea_en_el_cruce(self):
        self.cobro("300000.00")
        with self.assertLogs("sistema.terminales.servicios", "INFO"):
            self.assertTrue(avisar_objetivo(self.cuenta, D("299999.99")))

    def test_avisar_objetivo_sin_cruce_no_loguea(self):
        self.cobro("300000.00")
        with self.assertNoLogs("sistema.terminales.servicios", "INFO"):
            self.assertFalse(avisar_objetivo(self.cuenta, D("300000.00")))


class ReasignacionAvisoTest(SesionBase):
    """US5 / edge case: la corrección de sesión avisa si la cuenta nueva cruza su objetivo."""

    ahora = h(14)

    def setUp(self):
        super().setUp()
        Cuenta.objects.update(acumula_desde=h(0))
        self.cuenta_a.refresh_from_db()
        self.cuenta_b.refresh_from_db()
        self.a = self.abrir(inicio=h(9))
        cambiar_cuenta(self.terminal, self.cuenta_b, h(12), usuario=None)

    def cobro(self, cuenta, monto, fecha):
        return cobro_prueba(
            cuenta=cuenta,
            terminal=self.terminal,
            destinatario=cuenta.destinatario,
            monto=D(monto),
            estado="finalizado",
            metodo="debito",
            fecha=fecha,
        )

    def test_cuenta_nueva_que_cruza_el_objetivo_avisa_una_vez(self):
        self.cobro(self.cuenta_b, "290000.00", h(13))
        self.cobro(self.cuenta_a, "20000.00", h(11, 30))
        with self.assertLogs("sistema.terminales.servicios", "INFO") as log:
            corregir_cambio(self.a, h(11), usuario=None)
        self.assertEqual(len(log.records), 1)
        self.assertIn("alcanzó su objetivo", log.records[0].getMessage())
        self.assertEqual(acumulado(self.cuenta_b), D("310000.00"))

    def test_reasignacion_que_deja_la_cuenta_en_299999_99_no_avisa(self):
        self.cobro(self.cuenta_b, "290000.00", h(13))
        self.cobro(self.cuenta_a, "9999.99", h(11, 30))
        with self.assertNoLogs("sistema.terminales.servicios", "INFO"):
            corregir_cambio(self.a, h(11), usuario=None)
        self.assertEqual(acumulado(self.cuenta_b), D("299999.99"))

    def test_reasignacion_que_deja_la_cuenta_justo_en_el_objetivo_avisa(self):
        self.cobro(self.cuenta_b, "290000.00", h(13))
        self.cobro(self.cuenta_a, "10000.00", h(11, 30))
        with self.assertLogs("sistema.terminales.servicios", "INFO"):
            corregir_cambio(self.a, h(11), usuario=None)

    def test_la_cuenta_que_pierde_cobros_no_avisa(self):
        # b tiene 310000 en 12:30; se devuelve a la cuenta a (290000 + 310000 cruza) -> solo a avisa
        self.cobro(self.cuenta_a, "290000.00", h(10))
        self.cobro(self.cuenta_b, "20000.00", h(12, 30))
        with self.assertLogs("sistema.terminales.servicios", "INFO") as log:
            corregir_cambio(self.a, h(13), usuario=None)
        self.assertEqual(len(log.records), 1)
        self.assertEqual(log.records[0].args, (self.cuenta_a.pk,))


class AcumulaDesdeTest(AcumuladoBaseTest):
    def setUp(self):
        super().setUp()
        p = mock.patch("django.utils.timezone.now", return_value=AHORA)
        p.start()
        self.addCleanup(p.stop)

    def reiniciar(self, fecha):
        return cambiar_acumula_desde(self.cuenta, fecha, usuario=None)

    def test_reinicio_cuenta_solo_cobros_desde_la_fecha_y_deja_evento(self):
        self.cobro("320000.00", fecha=AHORA - timedelta(hours=1))
        self.cuenta.acumula_desde = AHORA - timedelta(days=1)
        self.cuenta.save()
        self.assertEqual(acumulado(self.cuenta), D("320000.00"))
        c = self.reiniciar(AHORA)
        self.assertEqual(acumulado(c), D("0.00"))
        self.cobro("5.00", fecha=AHORA)
        self.assertEqual(acumulado(c), D("5.00"))
        cambio = [e for e in historial_de(c) if e.tipo == "cambio"][0]
        self.assertEqual(cambio.cambios[0]["campo"], "acumula_desde")
        self.assertIsNotNone(cambio.cambios[0]["anterior"])
        self.assertIsNotNone(cambio.cambios[0]["nuevo"])

    def test_un_segundo_en_el_futuro_rechaza_y_ahora_exacto_acepta(self):
        with self.assertRaises(ValidationError):
            self.reiniciar(AHORA + timedelta(seconds=1))
        self.assertEqual(
            self.reiniciar(AHORA - timedelta(seconds=1)).acumula_desde, AHORA - timedelta(seconds=1)
        )
        self.assertEqual(self.reiniciar(AHORA).acumula_desde, AHORA)

    def test_cuenta_de_transferencia_rechaza(self):
        c = crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=self.d, titular="Titular Test",
            alias="alias.test.uno", tope_mensual=D("1000.00"),
        )  # fmt: skip
        with self.assertRaises(ValidationError):
            cambiar_acumula_desde(c, AHORA, usuario=None)

    def test_reinicio_no_avisa_el_objetivo(self):
        self.cobro("300000.00")
        with self.assertNoLogs("sistema.terminales.servicios", "INFO"):
            self.reiniciar(AHORA - timedelta(hours=1))

    def test_cruce_avisa_y_el_reinicio_deja_cero(self):
        Cuenta.objects.filter(pk=self.cuenta.pk).update(acumula_desde=AHORA - timedelta(days=1))
        self.cuenta.refresh_from_db()
        antes = acumulado(self.cuenta)
        self.cobro("300000.00", fecha=AHORA - timedelta(hours=1))
        with self.assertLogs("sistema.terminales.servicios", "INFO"):
            self.assertTrue(avisar_objetivo(self.cuenta, antes))
        self.assertEqual(acumulado(self.reiniciar(AHORA)), D("0.00"))
