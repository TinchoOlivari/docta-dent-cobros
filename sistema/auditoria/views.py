from datetime import timedelta

from django.apps import apps
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from sistema.auditoria.forms import FiltroEventosForm
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import ENTIDADES, historial_de, lineas_de_pagina, quien
from sistema.core.permisos import solo_administrador
from sistema.core.tiempo import inicio_dia_arg


def _pagina(request, qs):
    pagina = Paginator(qs.select_related("usuario"), 50).get_page(request.GET.get("page"))
    for e in pagina:
        e.quien = quien(e)
        e.etiqueta_entidad = ENTIDADES.get(e.entidad, e.entidad)
    lineas_de_pagina(pagina)
    return pagina


@solo_administrador
def historial(request, entidad, pk):
    if entidad not in ENTIDADES:
        raise Http404
    obj = get_object_or_404(apps.get_model(entidad), pk=pk)
    etiqueta, nombre = ENTIDADES[entidad], str(obj)
    # "Cobro 8 - ..." ya trae la entidad: no se repite (020/FR-024)
    titulo = nombre if nombre.startswith(etiqueta) else f"{etiqueta} {nombre}"
    return render(
        request,
        "auditoria/historial.html",
        {"titulo": titulo, "pagina": _pagina(request, historial_de(obj))},
    )


@solo_administrador
def eventos(request):
    form = FiltroEventosForm(request.GET or None)
    qs = Evento.objects.all()
    if form.is_bound and not form.is_valid():
        qs = qs.none()  # filtro inválido: 0 eventos (020/FR-024)
    elif form.is_bound:
        d = form.cleaned_data
        if d["usuario"] == "sistema":
            # un login fallido nunca es de "Sistema" (FR-009)
            qs = qs.filter(usuario__isnull=True).exclude(tipo=Evento.Tipo.LOGIN_FALLIDO)
        elif d["usuario"]:
            qs = qs.filter(usuario_id=d["usuario"])
        if d["entidad"]:
            qs = qs.filter(entidad=d["entidad"])
        if d["tipo"]:
            qs = qs.filter(tipo=d["tipo"])
        if d["desde"]:
            qs = qs.filter(fecha__gte=inicio_dia_arg(d["desde"]))
        if d["hasta"]:
            qs = qs.filter(fecha__lt=inicio_dia_arg(d["hasta"] + timedelta(days=1)))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "auditoria/eventos.html",
        {
            "form": form,
            "pagina": _pagina(request, qs),
            "filtros": params.urlencode(),
            "filtrado": any(params.values()),
            "limpiar": request.path,
        },
    )
