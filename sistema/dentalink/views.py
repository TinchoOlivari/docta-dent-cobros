import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Max
from django.http import Http404
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from sistema.cobros.models import ItemCobro

from sistema.core.permisos import puede_cobrar, solo_administrador

from . import servicios
from .cliente import DentalinkNoDisponible
from .forms import ConfiguracionForm
from .models import Banco, ConfiguracionDentalink, MedioPago, Profesional

logger = logging.getLogger(__name__)


def _no_disponible(request, exc):
    # 022/FR-006: el aviso va una sola vez, en la página; acá solo el log.
    logger.warning(
        "Dentalink no disponible: user=%s path=%s",
        getattr(request.user, "username", None),
        request.path,
    )


@puede_cobrar
def pacientes(request):
    q = request.GET.get("q", "").strip()
    lista = None
    no_disponible = False
    if q:
        try:
            lista = servicios.buscar_pacientes(q)
        except DentalinkNoDisponible as exc:
            _no_disponible(request, exc)
            no_disponible = True
    return render(
        request,
        "dentalink/pacientes.html",
        {
            "q": q,
            "pacientes": lista,
            "no_disponible": no_disponible,
            "cobro": request.GET.get("cobro", "") if request.GET.get("cobro", "").isdigit() else "",
        },
    )


@puede_cobrar
def paciente(request, id_paciente):
    tratamientos = []
    no_disponible = False
    try:
        tratamientos = servicios.prestaciones_de(id_paciente)
    except DentalinkNoDisponible as exc:
        _no_disponible(request, exc)
        no_disponible = True
    return render(
        request,
        "dentalink/paciente.html",
        {
            "tratamientos": tratamientos,
            **servicios.datos_paciente(id_paciente),
            "no_disponible": no_disponible,
        },
    )


@solo_administrador
def configuracion(request):
    form = ConfiguracionForm(request.POST or None, instance=ConfiguracionDentalink.actual())
    if form.is_valid():
        d = form.cleaned_data
        try:
            servicios.guardar_configuracion(usuario=request.user, **d)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Configuración guardada.")
            return redirect("dentalink:configuracion")
    modelos = (Banco, Profesional, MedioPago)
    copia = {
        "bancos": Banco.objects.filter(vigente=True).count(),
        "profesionales": Profesional.objects.filter(vigente=True).count(),
        "medios": MedioPago.objects.filter(vigente=True).count(),
        "ultima": max(
            (f for f in (m.objects.aggregate(f=Max("fecha_cambio"))["f"] for m in modelos) if f),
            default=None,
        ),
    }
    return render(request, "dentalink/configuracion.html", {"form": form, "copia": copia})


@solo_administrador
@require_POST
def actualizar(request):
    try:
        r = servicios.actualizar_copia(usuario=request.user)
    except DentalinkNoDisponible as exc:
        messages.error(request, str(exc))
        logger.warning("Copia de Dentalink no actualizada: user=%s", request.user.username)
    else:
        messages.success(
            request,
            f"Copia actualizada: {r['altas']} altas, {r['cambios']} cambios, {r['bajas']} bajas.",
        )
    return redirect("dentalink:configuracion")


def _mensaje_accion(accion, item):
    """FR-028: el texto dice el resultado, según el estado del ítem después de la acción."""
    Estado = ItemCobro.EstadoEnvio
    if accion == "marcar-enviado":
        return "Ítem marcado como enviado."
    if accion == "marcar-ajustado":
        return "Ítem marcado como ajustado."
    return {
        Estado.ENVIADO: "Pago enviado a Dentalink.",
        Estado.PENDIENTE: "Reintento en curso. Revisá el estado en unos minutos.",
        Estado.REVISAR: "Dentalink no confirmó el pago. Revisalo en Dentalink.",
        Estado.ERROR: "Dentalink rechazó el pago.",
    }.get(item.estado_envio, "Ítem actualizado.")


ACCIONES = {
    "reintentar": servicios.reintentar,
    "marcar-enviado": servicios.marcar_enviado,
    "marcar-ajustado": servicios.marcar_ajustado,
}


@solo_administrador
def envios(request):
    Estado = ItemCobro.EstadoEnvio
    estado = request.GET.get("estado", "")
    items = ItemCobro.objects.select_related("cobro").order_by("-fecha_cambio")
    if estado in Estado.values:
        items = items.filter(estado_envio=estado)
    else:
        estado = ""
        items = items.filter(estado_envio__in=[Estado.ERROR, Estado.REVISAR])
    pagina = Paginator(items, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "dentalink/envios.html",
        {"pagina": pagina, "estado": estado, "estados": Estado.choices},
    )


@solo_administrador
@require_POST
def accion(request, pk, accion):
    funcion = ACCIONES.get(accion)
    if funcion is None:
        raise Http404
    item = get_object_or_404(ItemCobro, pk=pk)
    Estado = ItemCobro.EstadoEnvio
    try:
        item = funcion(item, usuario=request.user)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    else:
        item.refresh_from_db()  # el envío puede haber cambiado el estado después de la acción
        mensaje = _mensaje_accion(accion, item)
        if item.estado_envio in (Estado.REVISAR, Estado.ERROR) and accion == "reintentar":
            messages.warning(request, mensaje)
        else:
            messages.success(request, mensaje)
    destino = reverse("dentalink:envios")
    estado = request.GET.get("estado")
    return redirect(f"{destino}?{urlencode({'estado': estado})}" if estado else destino)
