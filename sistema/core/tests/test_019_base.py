"""019 Fase 0: tono_avance, barra con tonos y <c-ayuda> sin tablas (FR-001, FR-002, FR-007, FR-008)."""

import re
from decimal import Decimal as D

from django.template import Context, Template


from sistema.core.templatetags.ui import tono_avance
from sistema.core.tests import SistemaTestCase

TOTAL = D("1000000.00")


def _plano(html):
    return re.sub(r"\s+", " ", html).strip()


def _barra(args):
    return Template("{% load ui %}{% barra " + args + " %}").render(Context())


class TonoAvanceTest(SistemaTestCase):
    def test_limites_con_objetivo_de_un_millon(self):
        casos = [
            ("0.00", "verde"),
            ("799999.99", "verde"),
            ("800000.00", "amarillo"),
            ("989999.99", "amarillo"),
            ("990000.00", "rojo"),
            ("1000000.00", "rojo"),
            ("1000000.01", "rojo"),
        ]
        for valor, tono in casos:
            with self.subTest(valor=valor):
                self.assertEqual(tono_avance(D(valor), TOTAL), tono)

    def test_total_cero_es_verde(self):
        self.assertEqual(tono_avance(D("5.00"), D("0.00")), "verde")
