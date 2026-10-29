from django.contrib import messages
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import F, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.timezone import localtime
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from sistema.cobros.models import Cobro
from sistema.core.models import MontoField
from sistema.destinatarios.models import Cuenta
from sistema.core.permisos import solo_administrador
from sistema.core.tiempo import ARG

from .forms import (
    AcumulaDesdeForm,
    AbrirSesionForm,
    CambiarCuentaForm,
    CerrarSesionForm,
    CorregirForm,
    EditarSesionForm,
    TerminalForm,
)
from .models import Sesion, Terminal
from . import servicios
from .servicios import cambiar_estado_terminal, crear_terminal, editar_terminal


def _guardar_form(form, accion):
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
    abiertas = Prefetch(
        "sesiones",
        queryset=Sesion.objects.filter(fin__isnull=True).select_related("cuenta__destinatario"),
        to_attr="abiertas",
    )
    terminales = Terminal.objects.select_related("proveedor").prefetch_related(abiertas)
    return render(request, "terminales/lista.html", {"terminales": terminales})


@solo_administrador
def nueva(request):
    form = TerminalForm(request.POST or None)
    if request.method == "POST":
        t = _guardar_form(form, lambda **d: crear_terminal(usuario=request.user, **d))
        if t:
            return redirect("terminales:detalle", t.pk)
    return render(
        request,
        "terminales/form.html",
        {"form": form, "titulo": "Nueva terminal", "cancelar": reverse("terminales:lista")},
    )


@solo_administrador
def detalle(request, pk):
    terminal = get_object_or_404(Terminal.objects.select_related("proveedor"), pk=pk)
    sesiones = list(terminal.sesiones.select_related("cuenta__destinatario").order_by("-inicio"))
    # Ordenadas de más nueva a más vieja: el par seguido es (sesiones[i+1], sesiones[i]).
    for nueva, vieja in zip(sesiones, sesiones[1:]):
        vieja.seguida = vieja.fin is not None and vieja.fin == nueva.inicio
    for s in sesiones:
        if getattr(s, "seguida", False):
            s.form_corregir = CorregirForm(
                initial={"hora": localtime(s.fin)}, auto_id=f"id_%s_{s.pk}"
            )
    ahora = timezone.localtime(timezone.now(), ARG)
    for s in sesiones:
        if s.fin is None:
            s.texto_cierre = (
                f"¿Cerrar la sesión de «{s.cuenta}» a las "
                f"{ahora:%H:%M}? Los cobros con tarjeta de esta terminal no van a tener "
                "cuenta hasta abrir otra."
            )
    return render(
        request,
        "terminales/detalle.html",
        {
            "terminal": terminal,
            "estados": Terminal.Estado.choices,
            "sesiones": sesiones,
            "abierta": any(s.fin is None for s in sesiones),
            "form_abrir": AbrirSesionForm(terminal=terminal),
            "form_cambiar": CambiarCuentaForm(terminal=terminal),
            "form_cerrar": CerrarSesionForm(),
        },
    )


@solo_administrador
def editar(request, pk):
    terminal = get_object_or_404(Terminal, pk=pk)
    form = TerminalForm(request.POST or None, instance=terminal)
    if request.method == "POST":
        t = _guardar_form(form, lambda **d: editar_terminal(terminal, usuario=request.user, **d))
        if t:
            return redirect("terminales:detalle", t.pk)
    return render(
        request,
        "terminales/form.html",
        {
            "form": form,
            "titulo": "Editar terminal",
            "editando": True,
            "cancelar": reverse("terminales:detalle", args=[terminal.pk]),
        },
    )


@solo_administrador
@require_POST
def estado(request, pk):
    terminal = get_object_or_404(Terminal, pk=pk)
    try:
        cambiar_estado_terminal(terminal, request.POST.get("estado"), usuario=request.user)
    except ValidationError as e:
        for mensaje in e.messages:
            messages.error(request, mensaje)
    return redirect("terminales:detalle", terminal.pk)


def _errores(request, e):
    for mensaje in e.messages:
        messages.error(request, mensaje)


def _accion(request, terminal, form, accion, exito):
    """Valida `form`, llama el servicio y vuelve al detalle con un mensaje."""
    if not form.is_valid():
        for errores in form.errors.values():
            for e in errores:
                messages.error(request, e)
    else:
        try:
            r = accion(**form.cleaned_data)
            messages.success(request, exito(r))
        except ValidationError as e:
            _errores(request, e)
    return redirect("terminales:detalle", terminal.pk)


@solo_administrador
@require_POST
def abrir(request, pk):
    terminal = get_object_or_404(Terminal, pk=pk)
    form = AbrirSesionForm(request.POST, terminal=terminal)
    return _accion(
        request,
        terminal,
        form,
        lambda **d: servicios.abrir_sesion(
            terminal, d["cuenta"], d["inicio"], usuario=request.user
        ),
        lambda r: "Sesión abierta.",
    )


@solo_administrador
@require_POST
def cambiar_cuenta(request, pk):
    terminal = get_object_or_404(Terminal, pk=pk)
    form = CambiarCuentaForm(request.POST, terminal=terminal)
    return _accion(
        request,
        terminal,
        form,
        lambda **d: servicios.cambiar_cuenta(
            terminal, d["cuenta"], d["hora"], usuario=request.user
        ),
        lambda r: "Cuenta cambiada.",
    )


@solo_administrador
@require_POST
def cerrar(request, pk):
    sesion = get_object_or_404(Sesion.objects.select_related("terminal"), pk=pk)
    return _accion(
        request,
        sesion.terminal,
        CerrarSesionForm(request.POST),
        lambda **d: servicios.cerrar_sesion(sesion, d["fin"], usuario=request.user),
        lambda r: "Sesión cerrada.",
    )


@solo_administrador
def editar_sesion(request, pk):
    sesion = get_object_or_404(Sesion.objects.select_related("terminal"), pk=pk)
    inicial = {"cuenta": sesion.cuenta, "inicio": localtime(sesion.inicio)}
    if sesion.fin:
        inicial["fin"] = localtime(sesion.fin)
    form = EditarSesionForm(request.POST or None, initial=inicial, terminal=sesion.terminal)
    if request.method == "POST" and form.is_valid():
        datos = {k: v for k, v in form.cleaned_data.items() if k != "fin" or v is not None}
        try:
            servicios.editar_sesion(sesion, usuario=request.user, **datos)
            messages.success(request, "Sesión editada.")
            return redirect("terminales:detalle", sesion.terminal_id)
        except ValidationError as e:
            for mensaje in e.messages:
                form.add_error(None, mensaje)
    return render(request, "terminales/sesion_form.html", {"form": form, "sesion": sesion})


@solo_administrador
@require_POST
def corregir(request, pk):
    sesion = get_object_or_404(Sesion.objects.select_related("terminal"), pk=pk)
    return _accion(
        request,
        sesion.terminal,
        CorregirForm(request.POST),
        lambda **d: servicios.corregir_cambio(sesion, d["hora"], usuario=request.user),
        lambda r: f"Cambio corregido. Cobros reasignados: {r}.",
    )


@solo_administrador
@require_POST
def borrar_sesion(request, pk):
    sesion = get_object_or_404(Sesion.objects.select_related("terminal"), pk=pk)
    try:
        servicios.borrar_sesion(sesion, usuario=request.user)
        messages.success(request, "Sesión borrada.")
    except ValidationError as e:
        _errores(request, e)
    return redirect("terminales:detalle", sesion.terminal_id)


@solo_administrador
def cuentas(request):
    # Una sola consulta con el acumulado (misma regla que servicios.acumulado, FR-062).
    acumulado = Sum(
        "cobros__monto",
        filter=Q(cobros__fecha__gte=F("acumula_desde")) & ~Q(cobros__estado=Cobro.Estado.CANCELADO),
    )
    lista_ = list(
        Cuenta.objects.filter(tipo=Cuenta.Tipo.TERMINAL_POS)
        .select_related("destinatario", "proveedor")
        .annotate(acumulado=Coalesce(acumulado, Value(Decimal("0.00")), output_field=MontoField()))
        .order_by("destinatario__nombre", "pk")
    )
    for c in lista_:
        c.form_desde = AcumulaDesdeForm(auto_id=f"id_%s_{c.pk}")
    return render(
        request,
        "terminales/cuentas.html",
        {"cuentas": lista_, "ahora": timezone.localtime(timezone.now(), ARG)},
    )


@solo_administrador
@require_POST
def acumula_desde(request, pk):
    cuenta = get_object_or_404(Cuenta, pk=pk, tipo=Cuenta.Tipo.TERMINAL_POS)
    form = AcumulaDesdeForm(request.POST)
    if not form.is_valid():
        for errores in form.errors.values():
            for e in errores:
                messages.error(request, e)
    else:
        try:
            servicios.cambiar_acumula_desde(
                cuenta, form.cleaned_data["fecha"], usuario=request.user
            )
            messages.success(request, "Acumulado reiniciado.")
        except ValidationError as e:
            _errores(request, e)
    return redirect("terminales:cuentas")
