"""020 B (FR-009): el portapapeles suma sin cancelados y marca los cancelados."""

from django.urls import reverse

from sistema.cobros.models import Cobro
from sistema.core.tests import SistemaTestCase
from sistema.reportes import servicios

from . import D, DatosMartinO


class PortapapelesSinCanceladosTest(DatosMartinO, SistemaTestCase):
    def texto(self):
        return servicios.texto_portapapeles(Cobro.objects.all())

    def test_total_excluye_cancelados_y_marca_dos_lineas(self):
        self.cobro(self.tincho, "27000.50", estado="cancelado")
        self.cobro(self.tincho, "0.01", estado="cancelado")  # un cobro de $0,00 no existe (CHECK)
        self.cobro(self.tincho, "3500.00", estado="finalizado")
        texto = self.texto()
        self.assertTrue(texto.endswith("Total sin cancelados $3.500,00"))
        self.assertEqual(sum(1 for r in texto.split("\n") if r.endswith("(Cancelado)")), 2)

    def test_solo_cancelados_da_cero(self):
        self.cobro(self.tincho, "1000.00", estado="cancelado")
        self.assertTrue(self.texto().endswith("Total sin cancelados $0,00"))

    def test_suma_pendiente_y_finalizado_con_centavo(self):
        self.cobro(self.tincho, "1000.00", estado="pendiente")
        self.cobro(self.tincho, "0.01", estado="finalizado")
        texto = self.texto()
        self.assertTrue(texto.endswith("Total sin cancelados $1.000,01"))
        self.assertNotIn("(Cancelado)", texto)

    def test_linea_usa_el_nombre_de_cuenta_unico(self):
        self.cobro(self.tincho, "10.00")
        self.assertIn(f" - {self.tincho} - ", self.texto())

    def test_total_coincide_con_el_subtotal_de_la_lista(self):
        self.cobro(self.tincho, "27000.50", estado="cancelado")
        self.cobro(self.tincho, "1000.00", estado="pendiente")
        self.cobro(self.galicia, "0.01", estado="finalizado")
        self.client.force_login(self.admin)
        subtotal = self.client.get(reverse("cobros:lista")).context["subtotal"]
        self.assertEqual(subtotal, D("1000.01"))
        self.assertTrue(self.texto().endswith("Total sin cancelados $1.000,01"))
