from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from sistema.asignacion.servicios import cupo_por_destinatario, semanas_por_cuenta
from sistema.automatizacion.models import Regla
from sistema.automatizacion.servicios import ultimas_ejecuciones
from sistema.core.permisos import solo_administrador
from sistema.core.tiempo import ARG

from .forms import (
    CuentaEfectivoEdicionForm,
    CuentaEfectivoForm,
    CuentaPosEdicionForm,
    CuentaPosForm,
    CuentaTransferenciaEdicionForm,
    CuentaTransferenciaForm,
    DestinatarioForm,
    ProveedorForm,
)
from .models import Cuenta, Destinatario, Proveedor
from .servicios import (
    cambiar_estado_cuenta,
    cambiar_estado_destinatario,
    cambiar_estado_destinatarios,
    crear_cuenta,
    crear_destinatario,
    crear_proveedor,
    editar_cuenta as editar_cuenta_servicio,
    desactivar_cuentas as desactivar_cuentas_servicio,
    editar_destinatario,
)

# tipo en la URL -> form
FORMS_CUENTA = {
    "transferencia": CuentaTransferenciaForm,
    "terminal_pos": CuentaPosForm,
    "efectivo": CuentaEfectivoForm,
}
FORMS_EDICION = {
    "transferencia": CuentaTransferenciaEdicionForm,
    "terminal_pos": CuentaPosEdicionForm,
    "efectivo": CuentaEfectivoEdicionForm,
}


def _guardar_form(form, accion):
    """Llama al servicio con los datos del form. Los errores del servicio van al form."""
    if not form.is_valid():
        return None
    try:
        return accion(**form.cleaned_data)
    except ValidationError as e:
        for mensaje in e.messages:
            form.add_error(None, mensaje)
        return None


@solo_administrador
def lista(request):
    destinatarios = Destinatario.objects.all()
    if request.GET.get("archivados") == "1":
        destinatarios = destinatarios.filter(estado="archivado")
    else:
        destinatarios = destinatarios.visibles()
    for filtro in ("tipo", "estado"):
        if request.GET.get(filtro):
            destinatarios = destinatarios.filter(**{filtro: request.GET[filtro]})
    q = request.GET.get("q", "").strip()
    if q:
        destinatarios = destinatarios.filter(
            Q(nombre__unaccent__icontains=q)
            | Q(cuentas__alias__unaccent__icontains=q)
            | Q(cuentas__cbu__icontains=q)
        ).distinct()
    destinatarios = list(destinatarios.order_by("nombre"))
    cupos = cupo_por_destinatario(destinatarios)
    for d in destinatarios:
        d.cupo = cupos.get(d.pk)
    return render(
        request,
        "destinatarios/lista.html",
        {
            "destinatarios": destinatarios,
            "q": q,
            "archivados": request.GET.get("archivados") == "1",
            "tipos": Destinatario.Tipo.choices,
            "estados": Destinatario.Estado.choices,
        },
    )


@solo_administrador
def nuevo(request):
    form = DestinatarioForm(request.POST or None)
    if request.method == "POST":
        d = _guardar_form(form, lambda **datos: crear_destinatario(usuario=request.user, **datos))
        if d:
            return redirect("destinatarios:detalle", d.pk)
    return render(
        request,
        "destinatarios/form.html",
        {"form": form, "titulo": "Nuevo destinatario", "cancelar": reverse("destinatarios:lista")},
    )


@solo_administrador
def detalle(request, pk):
    destinatario = get_object_or_404(Destinatario.objects.select_related("emisor"), pk=pk)
    mes = _mes(request.GET.get("mes", ""))
    # 1 consulta para las cuentas y 1 para sus topes, sin importar cuántas sean (FR-062)
    cuentas = list(
        destinatario.cuentas.select_related("destinatario", "proveedor")
        .prefetch_related("topes")
        .order_by("pk")
    )
    transferencias = [c for c in cuentas if c.tipo == Cuenta.Tipo.TRANSFERENCIA]
    reglas = (
        Regla.objects.filter(cuenta__destinatario=destinatario)
        .select_related("cuenta__destinatario")
        .order_by("hora", "pk")
    )
    reglas = list(reglas)
    ultimas = ultimas_ejecuciones(reglas)
    for r in reglas:
        r.ultima = ultimas.get(r.pk)
    return render(
        request,
        "destinatarios/detalle.html",
        {
            "destinatario": destinatario,
            "cuentas": cuentas,
            "cupo": cupo_por_destinatario([destinatario]).get(destinatario.pk),
            "mes": mes,
            "semanas": semanas_por_cuenta(transferencias, mes),
            "reglas": reglas,
        },
    )


def _mes(texto):
    """`AAAA-MM` -> primer día del mes. Inválido: el mes corriente en hora Argentina."""
    try:
        anio, mes = texto.split("-")
        if not 2000 <= int(anio) <= 2100:
            raise ValueError(anio)
        return date(int(anio), int(mes), 1)
    except (ValueError, OverflowError):
        hoy = timezone.now().astimezone(ARG).date()
        return hoy.replace(day=1)


@solo_administrador
def editar(request, pk):
    destinatario = get_object_or_404(Destinatario, pk=pk)
    form = DestinatarioForm(request.POST or None, instance=destinatario)
    if request.method == "POST":
        d = _guardar_form(
            form,
            lambda **datos: editar_destinatario(destinatario, usuario=request.user, **datos),
        )
        if d:
            return redirect("destinatarios:detalle", d.pk)
    return render(
        request,
        "destinatarios/form.html",
        {
            "form": form,
            "titulo": "Editar destinatario",
            "editando": True,
            "cancelar": reverse("destinatarios:detalle", args=[destinatario.pk]),
        },
    )


@solo_administrador
def nueva_cuenta(request, pk, tipo):
    destinatario = get_object_or_404(Destinatario, pk=pk)
    if tipo not in FORMS_CUENTA:
        raise Http404
    form = FORMS_CUENTA[tipo](request.POST or None, initial={"destinatario": destinatario})
    if request.method == "POST":
        cuenta = _guardar_form(
            form, lambda **datos: crear_cuenta(usuario=request.user, tipo=tipo, **datos)
        )
        if cuenta:
            return redirect("destinatarios:detalle", cuenta.destinatario_id)
    return render(
        request,
        "destinatarios/form.html",
        {
            "form": form,
            "titulo": "Nueva cuenta",
            "cancelar": reverse("destinatarios:detalle", args=[destinatario.pk]),
        },
    )


def _cambiar_estado(request, pk, modelo, servicio):
    obj = get_object_or_404(modelo, pk=pk)
    destino = obj if modelo is Destinatario else obj.destinatario
    try:
        servicio(obj, request.POST.get("estado"), usuario=request.user)
    except ValidationError as e:
        for mensaje in e.messages:
            messages.error(request, mensaje)
    return redirect("destinatarios:detalle", destino.pk)


@solo_administrador
@require_POST
def estado_destinatario(request, pk):
    return _cambiar_estado(request, pk, Destinatario, cambiar_estado_destinatario)


@solo_administrador
@require_POST
def estado_lote(request):
    estado = request.POST.get("estado")
    estados_validos = (
        Destinatario.Estado.ACTIVO,
        Destinatario.Estado.INACTIVO,
        Destinatario.Estado.ARCHIVADO,
    )
    if estado not in estados_validos:
        return HttpResponseBadRequest("Estado inválido.")
    pks = [int(p) for p in request.POST.getlist("pk") if p.isdigit()]
    cambiados, omitidos = cambiar_estado_destinatarios(pks, estado, usuario=request.user)
    messages.success(request, f"Cambiados: {len(cambiados)}. Omitidos: {len(omitidos)}.")
    destino = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        destino = reverse("destinatarios:lista")
    return redirect(destino)


@solo_administrador
@require_POST
def estado_cuenta(request, pk):
    return _cambiar_estado(request, pk, Cuenta, cambiar_estado_cuenta)


@solo_administrador
@require_POST
def desactivar_cuentas(request, pk):
    destinatario = get_object_or_404(Destinatario, pk=pk)
    try:
        n = desactivar_cuentas_servicio(destinatario, usuario=request.user)
    except ValidationError as e:
        for mensaje in e.messages:
            messages.error(request, mensaje)
    else:
        if n:
            messages.success(request, f"Se desactivaron {n} cuentas.")
        else:
            messages.info(request, "El destinatario no tiene cuentas activas.")
    return redirect("destinatarios:detalle", destinatario.pk)


@solo_administrador
def editar_cuenta(request, pk):
    cuenta = get_object_or_404(Cuenta, pk=pk)
    form = FORMS_EDICION[cuenta.tipo](request.POST or None, instance=cuenta)
    if request.method == "POST":
        guardada = _guardar_form(
            form, lambda **datos: editar_cuenta_servicio(cuenta, usuario=request.user, **datos)
        )
        if guardada:
            return redirect("destinatarios:detalle", guardada.destinatario_id)
    return render(
        request,
        "destinatarios/form.html",
        {
            "form": form,
            "titulo": "Editar cuenta",
            "editando": True,
            "cancelar": reverse("destinatarios:detalle", args=[cuenta.destinatario_id]),
        },
    )


@solo_administrador
def proveedores(request):
    form = ProveedorForm(request.POST or None)
    if request.method == "POST":
        if _guardar_form(form, lambda **datos: crear_proveedor(usuario=request.user, **datos)):
            messages.success(request, "Proveedor creado.")
            return redirect("destinatarios:proveedores")
    return render(
        request,
        "destinatarios/proveedores.html",
        {"form": form, "proveedores": Proveedor.objects.order_by("nombre")},
    )
