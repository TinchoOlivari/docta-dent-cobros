from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def pesos(valor):
    if valor is None:
        return "—"
    valor = Decimal(valor).quantize(Decimal("0.01"))
    texto = f"{abs(valor):,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
    return ("−$" if valor < 0 else "$") + texto


@register.filter
def monto_corto(valor):
    """Monto para tablas densas: `$150.000`, y los centavos solo si no son 00 (018/FR-023)."""
    if valor is None:
        return "—"
    texto = pesos(valor)
    return texto.removesuffix(",00")
