"""021 Fase C: esperado antes del primer tope y rótulos en los reportes (FR-025, FR-027)."""

from datetime import date, datetime
from decimal import Decimal as D

from django.urls import reverse

from sistema.cobros import servicios as cobros
from sistema.cobros.models import ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba, topes_desde_enero
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import TopeVigente
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario
from sistema.reportes import servicios

from . import DatosMartinO

SEP, OCT = date(2026, 9, 1), date(2026, 10, 1)


class EsperadoAntesDelPrimerTopeTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        topes_desde_enero()

    def desde(self, momento):
        TopeVigente.objects.filter(cuenta=self.tincho, tipo="mensual").update(desde=momento)

    def esperado(self, mes):
        """Esperado de la cuenta `tincho` en el reporte por destinatario y en Falta del mes."""
        rep = servicios.reporte_destinatario(self.martin, mes)
        fila = next(f for s in rep.secciones for f in s.cuentas if f.cuenta.pk == self.tincho.pk)
        falta = next(f for f in cobros.resumen_mes(mes) if f.pk == self.martin.pk)
        otras = D("500000.00") + D("300000.00") + D("250000.00")  # galicia y las 2 terminales
        self.assertEqual(falta.esperado - otras, fila.esperado)
        return fila.esperado

    def test_primer_registro_a_mitad_de_octubre(self):
        self.desde(datetime(2026, 10, 15, tzinfo=ARG))
        self.assertEqual(self.esperado(SEP), D("0.00"))
        self.assertEqual(self.esperado(OCT), D("100000.00"))

    def test_primer_registro_justo_al_empezar_octubre(self):
        self.desde(datetime(2026, 10, 1, 0, 0, tzinfo=ARG))
        self.assertEqual(self.esperado(SEP), D("0.00"))

    def test_primer_registro_un_segundo_antes_de_octubre(self):
        self.desde(datetime(2026, 9, 30, 23, 59, 59, tzinfo=ARG))
        self.assertEqual(self.esperado(SEP), D("100000.00"))

    def test_cuenta_migrada_sin_registros_usa_el_valor_actual(self):
        TopeVigente.objects.filter(cuenta=self.tincho)._raw_delete("default")
        self.tincho.refresh_from_db()
        self.assertEqual(self.esperado(SEP), D("100000.00"))

    def test_destinatario_creado_en_octubre_en_un_mes_viejo_no_espera_ni_le_falta(self):
        nuevo = crear_destinatario(usuario=None, nombre="Destinatario Ejemplo", tipo="profesional")
        cuenta = crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=nuevo, alias="ejemplo.uno",
            tope_mensual=D("1000.00"),
        )  # fmt: skip
        TopeVigente.objects.filter(cuenta=cuenta).update(desde=datetime(2026, 10, 1, tzinfo=ARG))
        rep = servicios.reporte_destinatario(nuevo, date(2020, 1, 1))
        self.assertEqual((rep.esperado, rep.diferencia), (D("0.00"), D("0.00")))


class RotulosTest(DatosMartinO, SistemaTestCase):
    def test_reporte_dice_recibido_con_el_monto_exacto(self):
        self.cobro(self.tincho, "630002.50")
        self.cobro(self.tincho, "60000.04", estado="pendiente")
        self.client.force_login(self.admin)
        r = self.client.get(
            reverse("reportes:destinatario", args=[self.martin.pk]), {"mes": "2026-10"}
        )
        self.assertContains(r, "$630.002,50")
        self.assertNotContains(r, "$690.002,54")  # lo reservado no es recibido


class BusquedaSinAcentosTest(SistemaTestCase):
    def test_items_por_paciente_ignora_acentos_y_mayusculas(self):
        dest = crear_destinatario(usuario=None, nombre="Destinatario Ejemplo", tipo="profesional")
        cuenta = crear_cuenta(usuario=None, tipo="efectivo", destinatario=dest)
        c = cobro_prueba(
            destinatario=dest, cuenta=cuenta, monto=D("10.00"), metodo="efectivo",
            nombre_paciente="Ibáñez Ejemplo", estado="finalizado",
            fecha=datetime(2026, 10, 2, tzinfo=ARG),
        )  # fmt: skip
        ItemCobro.objects.create(
            cobro=c, numero=1, monto=D("10.00"), id_profesional=7, nombre_profesional="Ejemplo"
        )
        for q in ("ibanez", "IBAÑEZ", "Ibáñez"):
            items, total = servicios.items_por_paciente(7, q)
            self.assertEqual((items.count(), total), (1, D("10.00")), q)
