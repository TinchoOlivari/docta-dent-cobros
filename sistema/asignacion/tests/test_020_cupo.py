from decimal import Decimal

from django.urls import reverse

from sistema.asignacion.servicios import cupo_por_destinatario
from sistema.destinatarios.servicios import crear_destinatario

from .test_asignar import DatosEjemplo

D = Decimal


class CupoExcedenteTest(DatosEjemplo):
    """Cuentas A ($1.000.000,00 de tope) y B ($10.000,00 de tope) del mismo destinatario (FR-010)."""

    def setUp(self):
        super().setUp()
        self.dx = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo", tipo="profesional"
        )
        self.a = self.transf(self.dx, "ejemplo.a", "1000000.00", "0.00", "0.00")
        self.b = self.transf(self.dx, "ejemplo.b", "10000.00", "0.00", "0.00")

    def cupo(self, consumido_b):
        if D(consumido_b):
            self.cobro(self.b, consumido_b)
        c = cupo_por_destinatario([self.dx])[self.dx.pk]
        self.assertEqual(c["restante"], c["tope"] - c["consumido"] + c["excedente"])
        return c

    def test_b_excedida_en_5000(self):
        c = self.cupo("15000.00")
        self.assertEqual((c["excedente"], c["restante"]), (D("5000.00"), D("1000000.00")))
        self.assertEqual(c["tope"], D("1010000.00"))
        self.assertEqual(c["consumido"], D("15000.00"))

    def test_justo_en_el_tope_no_hay_excedente(self):
        c = self.cupo("10000.00")
        self.assertEqual((c["excedente"], c["restante"]), (D("0.00"), D("1000000.00")))

    def test_un_centavo_arriba_del_tope(self):
        c = self.cupo("10000.01")
        self.assertEqual((c["excedente"], c["restante"]), (D("0.01"), D("1000000.00")))

    def test_un_centavo_abajo_del_tope(self):
        c = self.cupo("9999.99")
        self.assertEqual((c["excedente"], c["restante"]), (D("0.00"), D("1000000.01")))

    def test_sin_consumo_todo_es_restante(self):
        c = self.cupo("0.00")
        self.assertEqual((c["excedente"], c["restante"]), (D("0.00"), D("1010000.00")))

    def test_todas_excedidas_restante_cero(self):
        self.cobro(self.a, "1000001.00")
        c = self.cupo("15000.00")
        self.assertEqual((c["excedente"], c["restante"]), (D("1.00") + D("5000.00"), D("0.00")))

    def test_pantalla_muestra_excedente_solo_si_hay(self):
        self.client.force_login(self.user)
        self.cobro(self.b, "15000.00")
        r = self.client.get(reverse("destinatarios:lista"))
        self.assertContains(r, "Excedente $")
        self.assertContains(r, "5.000,00")

    def test_detalle_muestra_tope_consumido_excedente_y_restante(self):
        self.client.force_login(self.user)
        url = reverse("destinatarios:detalle", args=[self.dx.pk])
        r = self.client.get(url)
        self.assertContains(r, "Restante")
        self.assertNotContains(r, "Excedente")
        self.cobro(self.b, "15000.00")
        r = self.client.get(url)
        for texto in ("Tope", "Consumido", "Excedente", "Restante", "1.010.000,00", "5.000,00"):
            self.assertContains(r, texto)

    def test_detalle_con_consumo_justo_en_el_tope_no_muestra_excedente(self):
        self.client.force_login(self.user)
        self.cobro(self.b, "10000.00")
        r = self.client.get(reverse("destinatarios:detalle", args=[self.dx.pk]))
        self.assertNotContains(r, "Excedente")
