import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from sistema.cobros.models import Cobro
from sistema.core.permisos import filtro_cobros_visibles, solo_administrador
from sistema.reportes import descarga
from sistema.reportes.servicios import leer_mes

from .forms import DatosFacturaForm, EmisorForm
from .models import ComprobanteFiscal, Emisor
from .pdf import nombre_archivo, pdf_comprobante
from .servicios import (
    cambiar_estado_emisor,
    crear_emisor,
    editar_datos_factura,
    editar_emisor,
    emitir_nota_credito,
    estado_certificado,
    texto_desactivar,
    TITULOS_LIBRO,
    factura_vigente,
    libro_iva,
    pregunta_datos,
    reintentar as reintentar_comprobante,
    resolver_a_revisar,
)

logger = logging.getLogger(__name__)


def _libro(request):
    """(emisores, emisor, mes, error). Sin `emisor` en la URL: el primero. Inválido: `None` (FR-042)."""
    emisores = list(Emisor.objects.order_by("razon_social"))
    pedido = request.GET.get("emisor")
    if pedido is None:
        emisor = emisores[0] if emisores else None
    else:
        emisor = next((e for e in emisores if str(e.pk) == pedido), None)
    error = "Elegí un emisor de la lista." if emisor is None and emisores else ""
    return emisores, emisor, leer_mes(request.GET.get("mes")), error


@solo_administrador
def libro(request):
    emisores, emisor, mes, error = _libro(request)
    filas, totales = libro_iva(emisor, mes) if emisor else ([], None)
    return render(
        request,
        "arca/libro.html",
        {
            "emisores": emisores,
            "emisor": emisor,
            "error_emisor": error,
            "mes": mes,
            "titulos": TITULOS_LIBRO,
            "filas": filas,
            "totales": totales,
        },
    )


@solo_administrador
def libro_descargar(request):
    formato = request.GET.get("formato")
    _, emisor, mes, _ = _libro(request)
    if formato not in ("csv", "xlsx") or emisor is None:
        raise Http404
    filas, totales = libro_iva(emisor, mes)
    filas = [[f[0].strftime("%d/%m/%Y"), *f[1:]] for f in filas]  # FR-029
    pie = ["", "Totales", "", "", "", "", *totales, ""]
    return descarga.respuesta(
        "libro-iva",
        TITULOS_LIBRO,
        [*filas, pie],
        formato,
        nombre=f"libro-iva-{emisor.cuit}-{mes:%Y-%m}",
    )


def _guardar(request, form, accion):
    """Los errores del servicio van al form. Nunca se loguea `request.POST` ni `request.FILES`."""
    if not form.is_valid():
        return None
    try:
        return accion(**form.cleaned_data)
    except ValidationError as e:
        for mensaje in e.messages:
            form.add_error(None, mensaje)
        logger.warning("Emisor no guardado: user=%s path=%s", request.user.username, request.path)
        return None


def _avisar_vencido(request, emisor, subio_certificado):
    if subio_certificado and estado_certificado(emisor) == "vencido":
        messages.warning(
            request, "El certificado ya venció. Las facturas de este emisor van a fallar."
        )


@solo_administrador
def emisores(request):
    lista = list(Emisor.objects.order_by("razon_social"))
    for e in lista:
        e.cert = estado_certificado(e)
    return render(request, "arca/emisores.html", {"emisores": lista})


@solo_administrador
def nuevo_emisor(request):
    form = EmisorForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        emisor = _guardar(request, form, lambda **d: crear_emisor(usuario=request.user, **d))
        if emisor:
            messages.success(request, "Emisor creado.")
            _avisar_vencido(request, emisor, True)
            return redirect("arca:emisor", emisor.pk)
    return render(request, "arca/emisor_form.html", {"form": form, "titulo": "Nuevo emisor"})


@solo_administrador
def emisor(request, pk):
    emisor = get_object_or_404(Emisor, pk=pk)
    form = EmisorForm(request.POST or None, request.FILES or None, instance=emisor)
    if request.method == "POST":
        guardado = _guardar(
            request, form, lambda **d: editar_emisor(emisor, usuario=request.user, **d)
        )
        if guardado:
            messages.success(request, "Emisor guardado.")
            _avisar_vencido(request, guardado, bool(request.FILES.get("certificado")))
            return redirect("arca:emisor", emisor.pk)
    return render(
        request,
        "arca/emisor_form.html",
        {
            "form": form,
            "titulo": "Editar emisor",
            "emisor": emisor,
            "cert": estado_certificado(emisor),
            "texto_desactivar": texto_desactivar(emisor),
        },
    )


@solo_administrador
@require_POST
def estado_emisor(request, pk):
    emisor = get_object_or_404(Emisor, pk=pk)
    try:
        cambiar_estado_emisor(emisor, usuario=request.user, estado=request.POST.get("estado"))
    except ValidationError as e:
        for mensaje in e.messages:
            messages.error(request, mensaje)
    return redirect("arca:emisor", emisor.pk)


@solo_administrador
def pdf(request, pk):
    """FR-012. Solo comprobantes Emitidos de cobros que el usuario puede ver."""
    visibles = Cobro.objects.filter(filtro_cobros_visibles(request.user))
    comprobante = get_object_or_404(
        ComprobanteFiscal.objects.select_related("emisor"),
        pk=pk,
        estado=ComprobanteFiscal.Estado.EMITIDO,
        cobro__in=visibles,
    )
    respuesta = HttpResponse(pdf_comprobante(comprobante), content_type="application/pdf")
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre_archivo(comprobante)}"'
    return respuesta


def _decidir(request, pk, accion, exito):
    cobro = get_object_or_404(Cobro, pk=pk)
    try:
        accion(cobro)
    except ValidationError as e:
        logger.warning("Factura sin cambios: user=%s path=%s", request.user.username, request.path)
        messages.error(request, " ".join(e.messages))
    else:
        messages.success(request, exito)
    return redirect("cobros:detalle", pk=cobro.pk)


@solo_administrador
@require_POST
def nota_credito(request, pk):
    return _decidir(
        request,
        pk,
        lambda c: emitir_nota_credito(c, usuario=request.user),
        "Nota de crédito pedida.",
    )


@solo_administrador
@require_POST
def revisar(request, pk):
    emitir = request.POST.get("emitir")
    if emitir not in ("si", "no"):
        return HttpResponseBadRequest("Falta la respuesta sí o no.")
    return _decidir(
        request,
        pk,
        lambda c: resolver_a_revisar(c, usuario=request.user, emitir=emitir == "si"),
        "Factura revisada.",
    )


@solo_administrador
@require_POST
def reintentar(request, pk):
    comprobante = get_object_or_404(ComprobanteFiscal, pk=pk)
    try:
        reintentar_comprobante(comprobante, usuario=request.user)
    except ValidationError as e:
        logger.warning("Reintento sin hacer: user=%s path=%s", request.user.username, request.path)
        messages.error(request, " ".join(e.messages))
    else:
        messages.success(request, "Reintento pedido.")
    return redirect("cobros:detalle", pk=comprobante.cobro_id)


@solo_administrador
def datos_factura(request, pk):
    cobro = get_object_or_404(Cobro, pk=pk)
    factura = factura_vigente(cobro)
    if factura is None:
        raise Http404
    form = DatosFacturaForm(request.POST or None, factura=factura)
    if request.method == "POST" and form.is_valid():
        sin_cambios = all(getattr(factura, k) == v for k, v in form.datos().items())
        try:
            editar_datos_factura(
                cobro,
                usuario=request.user,
                nota_credito=form.nota_credito_elegida(),
                **form.datos(),
            )
        except ValidationError as e:
            logger.warning(
                "Datos de factura sin guardar: user=%s path=%s", request.user.username, request.path
            )
            form.add_error(None, e.messages)
        else:
            if sin_cambios:
                messages.info(request, "No hay cambios.")
            else:
                messages.success(request, "Datos de la factura guardados.")
            return redirect("cobros:detalle", pk=cobro.pk)
    emitida = factura.estado == ComprobanteFiscal.Estado.EMITIDO
    return render(
        request,
        "arca/datos_factura.html",
        {
            "cobro": cobro,
            "form": form,
            "emitida": emitida,
            "pregunta": pregunta_datos(factura) if emitida else "",
        },
    )
