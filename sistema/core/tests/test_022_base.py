"""022 Fase 0: barra sin porcentaje, CUIT y CBU, orden de selectores y piezas comunes."""

from decimal import Decimal as D

from django.template import Context, Template
from django_cotton.compiler_regex import CottonCompiler

from sistema.core.tests import SistemaTestCase


def _barra(valor):
    return Template("{% load ui %}{% barra valor total %}").render(
        Context({"valor": D(valor), "total": D("1000000.00")})
    )


def _componente(html, **contexto):
    """Dibuja un `<c-...>` como lo hace el cargador de plantillas."""
    return Template(CottonCompiler().process(html)).render(Context(contexto))


class BarraSinPorcentajeTest(SistemaTestCase):
    def test_excedido_llena_la_barra_y_avisa(self):
        html = _barra("1000000.01")
        self.assertIn("width: 100%", html)
        self.assertIn("(excedido)", html)
        self.assertIn("$1.000.000,01 de $1.000.000,00", html)

    def test_total_cero_no_dibuja(self):
        html = Template("{% load ui %}{% barra valor total %}").render(
            Context({"valor": D("0"), "total": D("0")})
        )
        self.assertEqual(html.strip(), "")
