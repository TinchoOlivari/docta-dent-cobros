"""023 Fase F: árbol de prestaciones con casilla (FR-034 a FR-040). Escritos sin correr (regla de Martin)."""

from unittest import mock

from django.urls import reverse

from sistema.cobros.models import Cobro, ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_efectivo import TRATAMIENTOS, BaseEfectivo
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento

NUEVO = f"{reverse('cobros:nuevo')}?paciente=501"
NINGUNA = "Elegí al menos una prestación."
VACIA = "Escribí el monto de esta prestación."
CERO = "El monto del ítem debe ser mayor que $0,00."
SALDO = "supera el saldo disponible de $12.000,50"


class BaseArbol(BaseEfectivo):
    """Una prestación con saldo de $12.000,50 en el tratamiento #16423."""

    def setUp(self):
        super().setUp()
        trat = {
            **tratamiento([prestacion(1, "12000.50", "0")]),
            "id": 16423,
            "nombre": "pruebaaaa",
        }
        p = mock.patch(TRATAMIENTOS, return_value=[trat])
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.ana)


class NuevoServidorTest(BaseArbol):
    """FR-037: el servidor toma solo lo marcado."""

    def enviar(self, monto, marcada=True, **extra):
        datos = {"metodo": "efectivo", "monto_1": monto, **extra}
        if marcada:
            datos["elegida_1"] = "on"
        return self.client.post(NUEVO, datos)

    def test_marcada_con_el_saldo_justo_da_un_item(self):
        self.enviar("12.000,50")
        self.assertEqual(ItemCobro.objects.get().monto, Cobro.objects.get().monto)
        self.assertEqual(str(Cobro.objects.get().monto), "12000.50")

    def test_marcada_con_un_centavo_mas_que_el_saldo_da_error_y_no_guarda(self):
        r = self.enviar("12.000,51")
        self.assertContains(r, SALDO)
        self.assertEqual(Cobro.objects.count(), 0)

    def test_marcada_con_un_centavo_da_un_item_de_un_centavo(self):
        self.enviar("0,01")
        self.assertEqual(str(ItemCobro.objects.get().monto), "0.01")

    def test_marcada_con_cero_da_el_error_de_monto_y_no_guarda(self):
        r = self.enviar("0,00")
        self.assertContains(r, CERO)
        self.assertEqual(Cobro.objects.count(), 0)

    def test_marcada_con_monto_vacio_da_el_error_del_campo(self):
        r = self.enviar("")
        self.assertContains(r, VACIA)
        self.assertEqual(Cobro.objects.count(), 0)

    def test_ninguna_marcada_con_monto_escrito_da_el_error_y_cero_cobros(self):
        r = self.enviar("12.000,50", marcada=False)
        self.assertContains(r, NINGUNA)
        self.assertEqual(Cobro.objects.count(), 0)
        self.assertEqual(ItemCobro.objects.count(), 0)

    def test_ninguna_marcada_y_sin_monto_da_el_error(self):
        r = self.enviar("", marcada=False)
        self.assertContains(r, NINGUNA)

    def test_con_dos_prestaciones_la_no_marcada_con_monto_se_ignora(self):
        trat = tratamiento([prestacion(1, "12000.50", "0"), prestacion(2, "5000.00", "0")])
        with mock.patch(TRATAMIENTOS, return_value=[trat]):
            self.client.post(
                NUEVO,
                {
                    "metodo": "efectivo",
                    "elegida_1": "on",
                    "monto_1": "12.000,50",
                    "monto_2": "5.000,00",
                },
            )
        self.assertEqual(ItemCobro.objects.count(), 1)
        self.assertEqual(str(Cobro.objects.get().monto), "12000.50")


class NuevoHtmlTest(BaseArbol):
    def test_la_rama_muestra_id_nombre_y_por_abonar(self):
        r = self.client.get(NUEVO)
        self.assertContains(r, "#16423: pruebaaaa")
        self.assertContains(r, "$12.000,50")  # 024/FR-003: subtotal sin la palabra «Saldo»

    def test_el_monto_lleva_el_saldo_en_centavos(self):
        self.assertContains(self.client.get(NUEVO), 'data-saldo="1200050"')


class ItemsTest(BaseArbol):
    """`cobros:items` usa el mismo árbol y las mismas reglas de FR-037."""

    def setUp(self):
        super().setUp()
        self.cobro = cobro_prueba(
            cuenta=self.efectivo,
            metodo="efectivo",
            estado="finalizado",
            monto="12000.50",
            faltan_items=True,
            creado_por=self.ana,
            nombre_paciente="Ana Inventada",
        )
        self.url = f"{reverse('cobros:items', args=[self.cobro.pk])}?paciente=501"

    def enviar(self, monto, marcada=True):
        datos = {"monto_1": monto}
        if marcada:
            datos["elegida_1"] = "on"
        return self.client.post(self.url, datos)

    def test_marcada_con_el_saldo_justo_guarda_el_item(self):
        self.enviar("12.000,50")
        self.assertEqual(self.cobro.items.count(), 1)

    def test_marcada_con_un_centavo_mas_da_error_de_saldo(self):
        self.assertContains(self.enviar("12.000,51"), SALDO)
        self.assertEqual(self.cobro.items.count(), 0)

    def test_marcada_con_un_centavo_no_suma_el_monto_del_cobro(self):
        self.assertContains(self.enviar("0,01"), "Los ítems suman")

    def test_marcada_con_cero_da_el_error_de_monto(self):
        self.assertContains(self.enviar("0,00"), CERO)

    def test_marcada_vacia_da_el_error_del_campo(self):
        self.assertContains(self.enviar(""), VACIA)

    def test_ninguna_marcada_da_el_error(self):
        r = self.enviar("12.000,50", marcada=False)
        self.assertContains(r, NINGUNA)
        self.assertEqual(self.cobro.items.count(), 0)
