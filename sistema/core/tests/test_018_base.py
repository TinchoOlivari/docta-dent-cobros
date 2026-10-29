"""018 Fase 0b: piezas compartidas de la UI."""

from decimal import Decimal as D

from django.template import engines

from sistema.core.templatetags.pesos import monto_corto
from sistema.core.tests import SistemaTestCase


def render(texto, **contexto):
    return engines["django"].from_string(texto).render(contexto)


class MontoCortoTest(SistemaTestCase):
    def test_siete_casos_de_la_spec(self):
        casos = [
            (D("150000.00"), "$150.000"),
            (D("150000.50"), "$150.000,50"),
            (D("0.01"), "$0,01"),
            (D("0.00"), "$0"),
            (D("999999999.99"), "$999.999.999,99"),
            (D("1000000.10"), "$1.000.000,10"),
            (None, "—"),
        ]
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                self.assertEqual(monto_corto(valor), esperado)
