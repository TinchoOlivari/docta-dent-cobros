from decimal import Decimal

from django.template import Context, Template

from sistema.core.tests import SistemaTestCase

CLAVE = "clave-de-siempre-1"
TOTAL = Decimal("1000000.00")


def _barra(**kw):
    kw = {k: Decimal(v) if v is not None else None for k, v in kw.items()}
    html = Template("{% load ui %}{% barra valor total restante %}").render(
        Context({"valor": None, "total": None, "restante": None, **kw})
    )
    return html


class BarraTest(SistemaTestCase):
    def test_limites_del_total(self):
        casos = [("0.00", "0"), ("999999.99", "99"), ("1000000.00", "100"), ("1000000.01", "100")]
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                html = _barra(valor=valor, total=TOTAL)
                self.assertIn(f'aria-valuenow="{esperado}"', html)
                self.assertEqual("(excedido)" in html, valor == "1000000.01")

    def test_total_cero_o_none_no_dibuja(self):
        self.assertEqual(_barra(valor="5", total="0").strip(), "")
        self.assertEqual(_barra(valor="5", total=None).strip(), "")

    def test_restante_250000_de_1000000_es_75(self):
        self.assertIn('aria-valuenow="75"', _barra(total=TOTAL, restante="250000.00"))
