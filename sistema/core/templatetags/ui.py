from decimal import ROUND_FLOOR, Decimal

from django import template
from django.forms import ChoiceField, DecimalField
from django.forms.widgets import Input, Select, Textarea
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

_TONOS = {
    **dict.fromkeys(
        ["finalizado", "registrado", "enviado", "ajustado", "emitido", "activo", "activa"], "ok"
    ),
    **dict.fromkeys(["pendiente", "revisar", "requiere_ajuste"], "alerta"),
    **dict.fromkeys(["error", "excedente"], "error"),
}


_NAVEGADORES = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SISTEMAS = (
    ("Windows", "Windows"),
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)


@register.filter
def dispositivo_corto(agente):
    """«Chrome · macOS» a partir del user-agent. Sin coincidencia, devuelve el texto tal cual."""
    nav = next((n for k, n in _NAVEGADORES if k in agente), "")
    so = next((n for k, n in _SISTEMAS if k in agente), "")
    return " · ".join(x for x in (nav, so) if x) or agente


@register.filter
def cuit(valor):
    """CUIT o CUIL de 11 dígitos como 20-00000000-1. Otro largo, tal cual (022/FR-010)."""
    v = str(valor or "")
    return f"{v[:2]}-{v[2:10]}-{v[10:]}" if len(v) == 11 and v.isdigit() else valor


@register.filter
def cbu(valor):
    """CBU o CVU de 22 dígitos en 2 bloques: 8 y 14. Otro largo, tal cual (022/FR-010)."""
    v = str(valor or "")
    return f"{v[:8]} {v[8:]}" if len(v) == 22 and v.isdigit() else valor


@register.filter
def tono(valor):
    return _TONOS.get(valor, "neutro")


_ICONOS = {"ok": "circle-check", "alerta": "clock", "error": "triangle-alert"}
_CANCELADOS = {"cancelado", "anulado"}


@register.inclusion_tag("sistema/componentes/_badge_estado.html")
def badge(valor, texto):
    t = tono(valor)
    icono = _ICONOS.get(t) or ("circle-x" if valor in _CANCELADOS else "circle")
    return {"tono": t, "texto": texto, "icono": icono}


# Clase de daisyUI por tipo de control, y la de error. Escritas completas (018/FR-003, FR-004).
_SIN_CLASE = {"checkbox", "radio", "file", "hidden", "submit", "button"}


def _clases_daisyui(widget, con_error):
    if isinstance(widget, Select):
        propia, error = "select", "select-error"
    elif isinstance(widget, Textarea):
        propia, error = "textarea", "textarea-error"
    elif isinstance(widget, Input) and widget.input_type not in _SIN_CLASE:
        propia, error = "input", "input-error"
    else:
        return ""
    return f"{propia} {error}" if con_error else propia


@register.filter
def campo_accesible(campo):
    """Dibuja el campo con `aria-invalid` y `aria-describedby` (FR-016). Salida segura de Django."""
    attrs = {}
    clases = _clases_daisyui(campo.field.widget, bool(campo.errors))
    if clases:
        previa = campo.field.widget.attrs.get("class", "")
        attrs["class"] = f"{previa} {clases}".strip()
    describe = []
    if campo.help_text:
        describe.append(f"{campo.auto_id}-ayuda")
    if campo.errors:
        attrs["aria-invalid"] = "true"
        describe.append(f"{campo.auto_id}-error")
    if describe:
        attrs["aria-describedby"] = " ".join(describe)
    f = campo.field
    if getattr(f, "empty_label", None) == "---------":  # ModelChoiceField
        f.empty_label = "Elegí…"
    elif isinstance(f, ChoiceField) and not hasattr(f, "queryset"):
        opciones = list(f.choices)
        if opciones and opciones[0] == ("", "---------"):
            f.choices = [("", "Elegí…"), *opciones[1:]]
    if isinstance(campo.field, DecimalField):
        attrs["inputmode"] = "decimal"  # teclado numérico con coma en el celular
    return campo.as_widget(attrs=attrs)


@register.inclusion_tag("sistema/componentes/_menu_item.html", takes_context=True)
def menu_item(context, nombre, texto, icono, *tambien):
    """Un destino del menú. `tambien` son otras vistas que lo dejan marcado como actual."""
    request = context.get("request")
    actual = getattr(getattr(request, "resolver_match", None), "view_name", None)
    return {
        "href": reverse(nombre),
        "texto": texto,
        "icono": icono,
        "actual": actual in (nombre, *tambien),
    }


@register.simple_block_tag
def seccion(content, titulo, plano=False):
    """Sección colapsable del menú. Abierta si contiene el destino actual. Sin JS, todo visible."""
    if plano:
        return format_html('<p class="nav-h">{}</p>{}', titulo, mark_safe(content))
    return render_to_string(
        "sistema/componentes/_menu_seccion.html",
        {
            "titulo": titulo,
            "contenido": mark_safe(content),
            "abierta": 'aria-current="page"' in content,
        },
    )


# Dibujan el bloque solo si la pantalla lo llenó (contrato de la base, 016).
@register.simple_block_tag
def h1_pagina(content):
    return format_html("<h1>{}</h1>", mark_safe(content.strip())) if content.strip() else ""


@register.simple_block_tag
def sub_pagina(content):
    return (
        format_html('<div class="sub">{}</div>', mark_safe(content.strip()))
        if content.strip()
        else ""
    )


@register.simple_block_tag
def migas_pagina(content):
    if not content.strip():
        return ""
    return format_html(
        '<ol class="crumbs"><li><a href="{}">Inicio</a></li>{}</ol>',
        reverse("usuarios:inicio"),
        mark_safe(content.strip()),
    )


def _ventana(actual, total, ancho=5):
    """Hasta `ancho` números alrededor de `actual`, corridos contra los bordes (FR-023)."""
    ini = max(1, min(actual - ancho // 2, total - ancho + 1))
    return range(ini, min(total, ini + ancho - 1) + 1)


@register.filter
def paginas(pagina):
    """Números de página para la barra: 1 2 3 4 5 … 162 / 1 … 78 79 80 81 82 … 162.
    `None` es un «…». Con 1 sola página, lista vacía."""
    total = pagina.paginator.num_pages
    if total <= 1:
        return []
    ventana = _ventana(pagina.number, total)
    salida = []
    if ventana[0] > 1:
        salida.append(1)
        if ventana[0] > 2:
            salida.append(None)
    salida.extend(ventana)
    if ventana[-1] < total:
        if ventana[-1] < total - 1:
            salida.append(None)
        salida.append(total)
    return salida


def tono_avance(valor, total):
    """Tono de una barra de avance (019/FR-008): verde < 80 %, amarillo < 99 %, rojo desde 99 %."""
    valor, total = Decimal(valor), Decimal(total)
    if total <= 0 or valor * 100 < total * 80:
        return "verde"
    return "amarillo" if valor * 100 < total * 99 else "rojo"


_TEXTO_TONO = {
    "verde": "En curso",
    "amarillo": "Cerca del objetivo",
    "rojo": "Objetivo alcanzado o casi",
}


@register.inclusion_tag("sistema/componentes/_barra_tope.html")
def barra(
    valor=None, total=None, restante=None, etiqueta="Avance del tope", tonos=True, texto=True
):
    if total is None or Decimal(total) <= 0:
        return {"dibujar": False}
    total = Decimal(total)
    valor = total - Decimal(restante) if restante is not None else Decimal(valor or 0)
    porcentaje = int((valor * 100 / total).to_integral_value(rounding=ROUND_FLOOR))
    return {
        "dibujar": True,
        "valor": valor,
        "total": total,
        "porcentaje": max(0, min(100, porcentaje)),
        "porcentaje_real": max(0, porcentaje),
        "etiqueta": etiqueta,
        "excedido": valor > total,
        "tono": tono_avance(valor, total) if tonos else "",
        "texto_tono": _TEXTO_TONO[tono_avance(valor, total)] if tonos else "",
        "texto": texto,
    }
