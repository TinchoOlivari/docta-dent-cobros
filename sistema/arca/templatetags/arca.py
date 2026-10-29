from django import template
from django.utils.timezone import localtime

from sistema.core.tiempo import ARG

from ..models import ComprobanteFiscal
from ..servicios import MAX_INTENTOS, _receptor, importes

register = template.Library()
DOCUMENTO = {96: "DNI", 80: "CUIT", 86: "CUIL"}


@register.filter
def detalle_factura(c):
    """FR-039: emisor, receptor e importes del comprobante, también si dio error."""
    _, tipo, numero = _receptor(c)
    neto, iva, total = (c.neto, c.iva, c.total)
    if total is None:
        neto, iva, total = importes(c, c.emisor)
    reintento = ""
    if c.estado == ComprobanteFiscal.Estado.PENDIENTE and c.intentos and c.proximo_intento:
        hora = localtime(c.proximo_intento, ARG).strftime("%H:%M")
        reintento = f"Reintentando: intento {c.intentos} de {MAX_INTENTOS}. Próximo a las {hora}."
    return {
        "cuit": c.emisor.cuit,
        "receptor": f"{DOCUMENTO[tipo]} {numero}" if tipo in DOCUMENTO else "Sin identificar",
        "neto": neto,
        "iva": iva,
        "total": total,
        "reintento": reintento,
    }
