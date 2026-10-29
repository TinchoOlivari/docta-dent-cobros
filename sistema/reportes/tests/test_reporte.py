from datetime import date, datetime

from sistema.cobros.servicios import resumen_mes
from sistema.cobros.tests.fabrica import pago_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Destinatario
from sistema.destinatarios.servicios import cambiar_estado_cuenta, crear_cuenta, crear_destinatario
from sistema.reportes.servicios import reporte_destinatario

from . import OCT, D, DatosMartinO

NOV = OCT.replace(month=11)


class ReporteTest(DatosMartinO, SistemaTestCase):
    def reporte(self, dest=None, mes=OCT):
        dest = dest or self.martin
        r = reporte_destinatario(dest, mes)
        fila = resumen_mes(mes, destinatarios=Destinatario.objects.filter(pk=dest.pk))[0]
        # SC-001: mismo total que 008 y la invariante de data-model.md
        self.assertEqual(r.recibido, fila.recibido)
        self.assertEqual(r.esperado, fila.esperado)
        self.assertEqual(r.diferencia, fila.falta)
        self.assertEqual(sum(s.recibido for s in r.secciones) + r.complementario, r.recibido)
        return r

    def filas(self, r):
        return {f.nombre: (f.recibido, f.esperado) for s in r.secciones for f in s.cuentas}

    def test_octubre_de_martin(self):
        self.cargar_octubre()
        r = self.reporte()
        self.assertEqual([s.nombre for s in r.secciones], ["Transferencias", "Terminal POS"])
        t, p = r.secciones
        self.assertEqual((t.recibido, t.esperado), (D("500000.00"), D("600000.00")))
        self.assertEqual((p.recibido, p.esperado), (D("500000.00"), D("550000.00")))
        self.assertEqual(
            self.filas(r),
            {
                "alias.uno": (D("100000.00"), D("100000.00")),
                "alias.dos": (D("400000.00"), D("500000.00")),
                "Martin MP": (D("300000.00"), D("300000.00")),
                "Martin Payway": (D("200000.00"), D("250000.00")),
            },
        )
        self.assertEqual(
            (r.recibido, r.esperado, r.diferencia),
            (D("1000000.00"), D("1150000.00"), D("150000.00")),
        )

    def test_pos_suma_monto_bruto(self):
        self.cobro(self.mp, "10000.00", monto_neto=D("9500.00"))
        self.assertEqual(self.reporte().recibido, D("10000.00"))

    def test_pago_complementario_cierra_la_diferencia(self):
        self.cargar_octubre()
        pago_prueba(
            destinatario=self.martin,
            monto=D("150000.00"),
            cuenta=self.galicia,
            creado_por=self.admin,
        )
        r = self.reporte()
        self.assertEqual(r.complementario, D("150000.00"))
        self.assertEqual((r.recibido, r.diferencia), (D("1150000.00"), D("0.00")))

    def test_pago_anulado_y_cobro_pendiente_no_suman(self):
        self.cargar_octubre()
        pago_prueba(
            destinatario=self.martin,
            monto=D("1.00"),
            cuenta=self.galicia,
            creado_por=self.admin,
            estado="anulado",
        )
        self.cobro(self.galicia, "50000.00", estado="pendiente")
        r = self.reporte()
        self.assertEqual((r.complementario, r.recibido), (D("0.00"), D("1000000.00")))

    def test_diferencia_negativa_en_el_borde(self):
        self.cargar_octubre()
        self.cobro(self.galicia, "150000.01")
        self.assertEqual(self.reporte().diferencia, D("-0.01"))

    def test_diferencia_un_centavo_arriba(self):
        self.cargar_octubre()
        self.cobro(self.galicia, "149999.99")
        self.assertEqual(self.reporte().diferencia, D("0.01"))

    def test_diferencia_cero_justo(self):
        self.cargar_octubre()
        self.cobro(self.galicia, "150000.00")
        self.assertEqual(self.reporte().diferencia, D("0.00"))

    def test_cuenta_inactiva_con_cobros_sale_con_esperado_cero(self):
        self.cargar_octubre()
        cambiar_estado_cuenta(self.galicia, "inactiva", usuario=None)
        r = self.reporte()
        self.assertEqual(self.filas(r)["alias.dos"], (D("400000.00"), D("0.00")))
        self.assertEqual(r.esperado, D("650000.00"))

    def test_cuenta_inactiva_sin_cobros_no_sale(self):
        cambiar_estado_cuenta(self.galicia, "inactiva", usuario=None)
        self.assertNotIn("alias.dos", self.filas(self.reporte()))

    def test_destinatario_sin_nada_da_cero(self):
        vacio = crear_destinatario(usuario=None, nombre="Vacío", tipo="profesional")
        r = self.reporte(vacio)
        self.assertEqual(r.secciones, [])
        self.assertEqual((r.recibido, r.esperado, r.diferencia), (D("0.00"),) * 3)

    def test_corte_de_mes_ultimo_y_primer_segundo(self):
        self.cobro(self.tincho, "100.00", fecha=datetime(2026, 10, 31, 23, 59, 59, tzinfo=ARG))
        self.cobro(self.tincho, "7.00", fecha=datetime(2026, 11, 1, 0, 0, tzinfo=ARG))
        self.assertEqual(self.reporte(mes=OCT).recibido, D("100.00"))
        self.assertEqual(self.reporte(mes=NOV).recibido, D("7.00"))

    def test_cuenta_que_cambio_de_destinatario(self):
        jimena = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        cuenta = self.transf(jimena, "jimena.alias", "200000.00")
        self.cobro(cuenta, "70000.00", destinatario=self.martin)
        self.cobro(cuenta, "30000.00", destinatario=jimena)
        self.assertEqual(self.filas(self.reporte())["jimena.alias"], (D("70000.00"), D("0.00")))
        self.assertEqual(
            self.filas(self.reporte(jimena))["jimena.alias"], (D("30000.00"), D("200000.00"))
        )

    def test_efectivo_tiene_seccion_sin_esperado(self):
        clinica = crear_destinatario(usuario=None, nombre="Clínica", tipo="clinica")
        caja = crear_cuenta(usuario=None, tipo="efectivo", destinatario=clinica)
        self.cobro(caja, "5000.00", metodo="efectivo", destinatario=clinica)
        r = self.reporte(clinica)
        efectivo = r.secciones[-1]
        self.assertEqual(
            (efectivo.nombre, efectivo.recibido, efectivo.esperado),
            ("Efectivo", D("5000.00"), None),
        )
        self.assertNotIn("Efectivo", [s.nombre for s in self.reporte().secciones])


class ReporteTopeDelMesTest(DatosMartinO, SistemaTestCase):
    def test_mes_cerrado_espera_el_tope_de_ese_mes_aunque_el_tope_cambio_despues(self):
        from sistema.destinatarios.servicios import editar_cuenta

        for c in (self.tincho, self.galicia):
            c.topes.update(desde=datetime(2026, 8, 1, tzinfo=ARG))
        editar_cuenta(self.galicia, usuario=None, tope_mensual=D("200000.00"))
        sep = date(2026, 9, 1)
        r = reporte_destinatario(self.martin, sep)
        fila = resumen_mes(sep, destinatarios=Destinatario.objects.filter(pk=self.martin.pk))[0]
        esperado = {f.nombre: f.esperado for s in r.secciones for f in s.cuentas}
        self.assertEqual(esperado["alias.dos"], D("500000.00"))
        self.assertEqual(r.esperado, fila.esperado)
        self.assertEqual(r.esperado, D("1150000.00"))
