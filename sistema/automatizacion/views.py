from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from sistema.core.permisos import solo_administrador

from .forms import ReglaForm
from .models import Regla
from .servicios import cambiar_estado_reglas, crear_regla, editar_regla, ultimas_ejecuciones


@solo_administrador
def lista(request):
    reglas = Regla.objects.select_related("cuenta__destinatario")
    q = request.GET.get("q", "").strip()
    if q:
        reglas = reglas.filter(
            Q(nombre__unaccent__icontains=q)
            | Q(cuenta__alias__unaccent__icontains=q)
            | Q(cuenta__destinatario__nombre__unaccent__icontains=q)
        )
    filtros = {}
    for campo, opciones in (
        ("estado", Regla.Estado),
        ("disparador", Regla.Disparador),
        ("accion", Regla.Accion),
    ):
        valor = request.GET.get(campo, "")
        if valor in opciones.values:  # fuera de choices: sin filtro
            reglas = reglas.filter(**{campo: valor})
        else:
            valor = ""
        textos = (
            [(v, Regla.TEXTO_ACCION[v]) for v in opciones.values]
            if campo == "accion"
            else opciones.choices
        )
        filtros[campo] = (valor, textos)
    reglas = list(reglas)
    ultimas = ultimas_ejecuciones(reglas)
    for r in reglas:
        r.ultima = ultimas.get(r.pk)
    contexto = {
        "reglas": reglas,
        "q": q,
        "filtros": filtros,
        "filtrado": bool(q or any(v for v, _ in filtros.values())),
    }
    return render(request, "automatizacion/lista.html", contexto)


def _errores(form, e):
    if hasattr(e, "error_dict"):
        for campo, errores in e.message_dict.items():
            destino = campo if campo in form.fields else None
            for mensaje in errores:
                form.add_error(destino, mensaje)
    else:
        for mensaje in e.messages:
            form.add_error(None, mensaje)


def _formulario(request, regla, titulo, guardar, mensaje):
    form = ReglaForm(request.POST or None, instance=regla)
    if request.method == "POST" and form.is_valid():
        try:
            guardar(form.datos_servicio())
        except ValidationError as e:
            _errores(form, e)
        else:
            messages.success(request, mensaje)
            return redirect("automatizacion:lista")
    # el resumen describe la regla guardada, no la del formulario (FR-015)
    resumen = Regla.objects.get(pk=regla.pk).descripcion if regla else ""
    return render(
        request,
        "automatizacion/form.html",
        {"form": form, "titulo": titulo, "resumen": resumen, "regla": regla},
    )


@solo_administrador
def nueva(request):
    return _formulario(
        request,
        None,
        "Nueva regla",
        lambda datos: crear_regla(usuario=request.user, **datos),
        "Regla creada.",
    )


@solo_administrador
def editar(request, pk):
    regla = get_object_or_404(Regla, pk=pk)
    return _formulario(
        request,
        regla,
        "Editar regla",
        lambda datos: editar_regla(regla, usuario=request.user, **datos),
        "Regla guardada.",
    )


@solo_administrador
@require_POST
def estado(request):
    nuevo = request.POST.get("estado")
    if nuevo not in ("activa", "inactiva"):
        return HttpResponseBadRequest("Estado inválido.")
    pks = [p for p in request.POST.getlist("pks") if p.isdigit()]
    if not pks:
        messages.error(request, "Elegí al menos una regla.")
        return redirect("automatizacion:lista")
    reglas = Regla.objects.filter(pk__in=pks)
    try:
        n = cambiar_estado_reglas(list(reglas), nuevo, usuario=request.user)
    except ValidationError as e:
        for mensaje in e.messages:
            messages.error(request, mensaje)
    else:
        if nuevo == "activa":
            texto = "1 regla activada." if n == 1 else f"{n} reglas activadas."
        else:
            texto = "1 regla desactivada." if n == 1 else f"{n} reglas desactivadas."
        messages.success(request, texto)
    return redirect("automatizacion:lista")
