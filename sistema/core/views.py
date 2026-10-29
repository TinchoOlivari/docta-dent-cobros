from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render

from sistema.core.permisos import solo_administrador


class _Demo(forms.Form):
    nombre = forms.CharField(label="Nombre", help_text="Como figura en el DNI.")
    monto = forms.CharField(label="Monto")
    comprobante = forms.FileField(label="Comprobante", required=False)
    bloqueado = forms.CharField(label="Campo deshabilitado", disabled=True, initial="No editable")


class _DemoAyuda(forms.Form):
    nombre = forms.CharField(label="Nombre con ayuda")
    casilla = forms.BooleanField(label="Casilla de ejemplo", required=False)


def catalogo(request):
    """Catálogo de componentes (FR-034). Solo con DEBUG: sin DEBUG, 404 para todos (021/FR-053)."""
    if not settings.DEBUG:
        raise Http404
    return _catalogo(request)


@solo_administrador
def _catalogo(request):
    pagina = Paginator(range(1, 121), 50).page(2)
    contexto = {
        "pagina": pagina,
        "form": _Demo(),
        "form_ayuda": _DemoAyuda(),
        "form_error": _Demo({"nombre": "", "monto": ""}),
        "badges": [
            ("pendiente", "Pendiente"),
            ("finalizado", "Finalizado"),
            ("cancelado", "Cancelado"),
            ("excedente", "Excedente"),
            ("revisar", "Factura a revisar"),
            ("error", "Factura con error"),
            ("revisado", "Revisado"),
        ],
    }
    return render(request, "sistema/catalogo.html", contexto)
