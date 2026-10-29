import logging
from datetime import datetime
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import DecimalField, Exists, OuterRef, Sum
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponseBadRequest
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from sistema.arca.models import ComprobanteFiscal
from sistema.arca.servicios import factura_emitida, sin_factura_vigente
from sistema.asignacion.servicios import SinCuentaDisponible
from sistema.auditoria.servicios import historial_de
from sistema.core.archivos import servir
from sistema.core.templatetags.pesos import pesos
from sistema.core.permisos import filtro_cobros_visibles, puede_cobrar, solo_administrador
from sistema.dentalink.cliente import DentalinkNoDisponible
from sistema.dentalink.servicios import datos_paciente, nacimiento_paciente
from sistema.destinatarios.models import Destinatario
from sistema.reportes import descarga
from sistema.reportes.filtros import FiltroCobros, FiltroCobrosOperador
from sistema.terminales.servicios import terminales_disponibles
from sistema.usuarios import roles

from . import servicios
from .forms import (
    AnularPagoForm,
    CobroForm,
    EditarCobroForm,
    FaltanItemsForm,
    ObservacionesForm,
    PagoComplementarioForm,
)
from .models import METODOS_POS, Cobro, ComprobanteCobro, PagoComplementario

logger = logging.getLogger(__name__)


def _visibles(request):
    return Cobro.objects.filter(filtro_cobros_visibles(request.user))


def _log(request, error):
    logger.warning(
        "Error al guardar el cobro: user=%s path=%s error=%s",
        getattr(request.user, "username", None),
        request.path,
        " ".join(error.messages) if hasattr(error, "messages") else error,
    )


def _sin_pagina(request, *quitar):
    get = request.GET.copy()
    get.pop("page", None)
    for n in quitar:
        get.pop(n, None)
    get.pop("pk", None)  # la selección que vuelve de un error no es un filtro
    return get.urlencode()


# Columnas ordenables de la lista (017/FR-019, 018/FR-025). Valor fuera de la lista: orden por defecto.
# `fecha` ordena por la creación (`fecha_alta`), la misma fecha que muestra la columna (FR-033).
ORDEN_OPERADOR = {
    "fecha": "fecha_alta",
    "paciente": "nombre_paciente",
    "estado": "estado",
    "monto": "monto",
}
ORDEN_ADMIN = {
    **ORDEN_OPERADOR,
    "destinatario": "destinatario__nombre",
    "revisado": "revisado",
    "creador": "creado_por__username",
    "numero": "pk",
    # Enlace viejo: se acepta, sin encabezado.
    "cuenta": "cuenta__alias",
}


def _anotadas(cobros):
    """Las marcas de factura que muestra la lista del administrador."""
    facturas = ComprobanteFiscal.objects.filter(cobro=OuterRef("pk"))
    return cobros.annotate(
        factura_con_error=Exists(facturas.filter(estado="error")),
        factura_a_revisar=Exists(facturas.filter(tipo="factura", vigente=True, a_revisar=True)),
        factura_pendiente=Exists(facturas.filter(estado="pendiente", vigente=True)),
    )


def _decorar(c):
    """Lo que la fila del administrador muestra y no es un campo: ícono de estado y profesionales."""
    c.nivel, c.motivos = servicios.estado_visual(c)
    c.monto_centavos = int(c.monto * 100)  # para el resumen de "Cancelar elegidos" (FR-021)
    nombres = dict.fromkeys(i.nombre_profesional for i in c.items.all() if i.nombre_profesional)
    c.profesionales = ", ".join(nombres)


def _orden(request, permitidos):
    """(`orden` válido o "", campo de la base o None)."""
    pedido = request.GET.get("orden", "")
    campo = permitidos.get(pedido.lstrip("-"))
    return (pedido, campo) if campo else ("", None)


@puede_cobrar
def lista(request):
    admin = request.user.role == roles.ADMIN
    cobros = _visibles(request).select_related("cuenta", "creado_por", "destinatario")
    pendientes = False
    subtotal = None
    if admin:  # FR-045: los filtros y las marcas son del administrador
        filtro = FiltroCobros(request.GET, queryset=_anotadas(cobros))
        cobros = filtro.qs.prefetch_related("items")
        subtotal = cobros.exclude(estado=Cobro.Estado.CANCELADO).aggregate(
            t=Coalesce(Sum("monto"), Decimal("0.00"), output_field=DecimalField())
        )["t"]
    else:
        filtro = FiltroCobrosOperador(request.GET, queryset=cobros)
        cobros = filtro.qs
        pendientes = filtro.form.cleaned_data.get("estado") == "pendiente"
    orden, campo = _orden(request, ORDEN_ADMIN if admin else ORDEN_OPERADOR)
    if campo:
        cobros = cobros.order_by(f"-{campo}" if orden.startswith("-") else campo, "-pk")
    elif pendientes:  # del más viejo al más nuevo, por la fecha de la ventana de 15 días (FR-033)
        cobros = cobros.order_by("fecha_alta", "pk")
    else:
        cobros = cobros.order_by("-fecha_alta", "-pk")
    pagina = Paginator(cobros, 50).get_page(request.GET.get("page"))
    if admin:
        for c in pagina:
            _decorar(c)
    sin_orden = request.GET.copy()
    sin_orden.pop("orden", None)
    sin_orden.pop("page", None)
    # `orden` y `page` no cuentan como filtro (018/FR-020).
    hay_filtros = any(request.GET.get(n, "").strip() for n in filtro.filters)
    # P8: solo la tabla si pide HTMX; "Atrás" (restauración de historial) pide la página entera.
    parcial = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
    contexto = {
        "pagina": pagina,
        "es_admin": admin,
        "filtro": filtro,
        "query": _sin_pagina(request),
        "query_sin_por": _sin_pagina(request, "por"),
        "query_sin_orden": sin_orden.urlencode(),
        "orden": orden,
        "subtotal": subtotal,
        "pendientes_operador": pendientes,
        "hay_filtros": hay_filtros,
        "parcial": parcial,
        "next_url": request.get_full_path(),
        "motivos": Cobro.MotivoCancelacion.choices,
        "elegidos": {int(p) for p in request.GET.getlist("pk") if p.isdigit()},
    }
    plantilla = "cobros/_tabla.html" if parcial else "cobros/lista.html"
    respuesta = render(request, plantilla, contexto)
    patch_vary_headers(respuesta, ["HX-Request"])
    return respuesta


@puede_cobrar
def nuevo(request):
    if "cobro" in request.GET:  # 024/FR-014: transferencia ya asignada, vista bloqueada
        return _nuevo_asignado(request, request.GET["cobro"])
    disponibles = {m: list(terminales_disponibles(m)) for m in METODOS_POS}
    id_paciente = request.GET.get("paciente", "")
    inicial = {"metodo": request.GET.get("metodo") or Cobro.Metodo.EFECTIVO}  # 024/FR-008
    # Métodos POS sin terminal con sesión abierta: la plantilla avisa y deshabilita Guardar (FR-026).
    contexto = {
        "id_paciente": id_paciente,
        "sin_terminal": [m for m in METODOS_POS if not disponibles[m]],
    }
    if request.GET.get("sin_dentalink"):
        form = FaltanItemsForm(
            request.POST or None,
            request.FILES or None,
            initial=inicial,
            metodos_pos_terminales=disponibles,
        )
        contexto["sin_dentalink"] = True
    elif id_paciente.isdigit():
        try:
            prestaciones = servicios.prestaciones_para_cobrar(int(id_paciente))
        except DentalinkNoDisponible as e:
            messages.warning(request, str(e))
            return render(request, "cobros/nuevo.html", {**contexto, "caido": True})
        contexto.update(datos_paciente(int(id_paciente)))  # 1 pedido, no 3
        form = CobroForm(
            request.POST or None,
            request.FILES or None,
            prestaciones=prestaciones,
            initial=inicial,
            metodos_pos_terminales=disponibles,
        )
    else:
        return redirect("dentalink:pacientes")
    contexto["form"] = form
    contexto["guardar_deshabilitado"] = form["metodo"].value() in contexto["sin_terminal"]
    if request.method == "POST" and form.is_valid():
        datos = form.datos_metodo()
        if "nombre_paciente" in form.fields:
            datos.update(
                monto=form.cleaned_data["monto"],
                nombre_paciente=form.cleaned_data["nombre_paciente"],
            )
        else:
            items = form.items()
            datos.update(
                id_paciente=int(id_paciente),
                items=items,
                monto=sum((i["monto"] for i in items), Decimal("0.00")),
            )
        try:
            cobro = servicios.crear_cobro(usuario=request.user, **datos)
        except SinCuentaDisponible as e:  # 024/FR-015: no se guardó; lo dice la caja del método
            _log(request, e)
            contexto["sin_cupo"] = datos["monto"]
        except (ValidationError, DentalinkNoDisponible) as e:
            _log(request, e)
            form.add_error(None, e.messages if isinstance(e, ValidationError) else str(e))
        else:
            if cobro.metodo == Cobro.Metodo.TRANSFERENCIA:  # 024/FR-014: sigue en esta pantalla
                return redirect(f"{reverse('cobros:nuevo')}?cobro={cobro.pk}")
            messages.success(request, "Cobro guardado.")
            return redirect("cobros:detalle", pk=cobro.pk)
    if request.method == "POST" and request.FILES.get("comprobante") and form.errors:
        # 021/FR-006: el navegador no conserva el archivo elegido al volver con un error.
        if "comprobante" not in form.errors:
            form.add_error("comprobante", "Volvé a elegir el comprobante.")
    return render(request, "cobros/nuevo.html", contexto)


def _nuevo_asignado(request, pk, *, extra=None, status=200):
    """024/FR-016 y FR-019: la transferencia Pendiente con su cuenta, sin poder cambiar nada."""
    if not str(pk).isdigit():
        raise Http404
    cobro = get_object_or_404(_visibles(request).select_related("cuenta"), pk=pk)
    if cobro.metodo != Cobro.Metodo.TRANSFERENCIA or cobro.estado != Cobro.Estado.PENDIENTE:
        return redirect("cobros:detalle", pk=cobro.pk)
    id_paciente = cobro.id_paciente_dentalink
    items = list(cobro.items.order_by("numero"))
    inicial = {"metodo": Cobro.Metodo.TRANSFERENCIA}
    for i in items:
        inicial.update(
            {
                f"elegida_{i.id_prestacion}": True,
                f"monto_{i.id_prestacion}": i.monto,
                f"profesional_{i.id_prestacion}": i.id_profesional,
            }
        )
    prestaciones = []
    if id_paciente is not None:  # «Sin vincular a Dentalink»: sin prestaciones
        try:
            prestaciones = servicios.prestaciones_para_cobrar(id_paciente, cobro=cobro)
        except DentalinkNoDisponible:  # la tabla muestra solo los ítems del cobro
            pass
    form = CobroForm(prestaciones=prestaciones, initial=inicial, metodos_pos_terminales={})
    for nombre, campo in form.fields.items():
        campo.disabled = nombre != "comprobante"  # FR-016: solo falta el comprobante
    contexto = {
        "form": form,
        "cobro": cobro,
        "asignado": True,
        "items": items,
        "id_paciente": id_paciente,
        "nombre_paciente": cobro.nombre_paciente,
        "dni_paciente": cobro.dni_paciente,
        "nacimiento_paciente": id_paciente and nacimiento_paciente(id_paciente),
        "sin_dentalink": id_paciente is None,
        "lineas_cuenta": _lineas_cuenta(cobro),
        "motivos": Cobro.MotivoCancelacion.choices,
        "sin_terminal": [],
        **(extra or {}),
    }
    return render(request, "cobros/nuevo.html", contexto, status=status)


@puede_cobrar
@require_POST
def registrar_transferencia(request, pk):
    """024/FR-017: el comprobante cierra la transferencia asignada en `cobros:nuevo`."""
    cobro = get_object_or_404(_visibles(request), pk=pk)
    try:
        servicios.adjuntar_comprobante(
            cobro,
            request.FILES.get("comprobante"),
            usuario=request.user,
            estado_visto=request.POST.get("estado_visto", ""),
        )
    except ValidationError as e:
        _log(request, e)
        cobro.refresh_from_db()
        if cobro.estado == Cobro.Estado.PENDIENTE:
            extra = {"error_comprobante": " ".join(e.messages)}
            return _nuevo_asignado(request, pk, extra=extra, status=400)
        messages.error(request, " ".join(e.messages))
        return redirect("cobros:detalle", pk=cobro.pk)
    messages.success(request, "Cobro guardado.")
    return redirect("cobros:detalle", pk=cobro.pk)


@puede_cobrar
@require_POST
def empezar_de_nuevo(request, pk):
    """024/FR-018: cancela la transferencia asignada con el motivo que elige el usuario."""
    cobro = get_object_or_404(_visibles(request), pk=pk)
    motivo = request.POST.get("motivo", "")
    detalle = request.POST.get("detalle", "").strip()
    errores = {}
    if not motivo.strip():
        errores["motivo"] = "Elegí un motivo de cancelación."
    elif motivo == Cobro.MotivoCancelacion.OTRO and not detalle:
        errores["detalle"] = "Escribí el detalle del motivo."
    if not errores:
        try:
            servicios.cancelar_cobro(
                cobro,
                usuario=request.user,
                motivo=motivo,
                detalle=detalle,
                estado_visto=request.POST.get("estado_visto", ""),
            )
        except ValidationError as e:
            _log(request, e)
            errores["motivo"] = " ".join(e.messages)
        else:
            messages.success(request, "Cobro cancelado. Podés empezar de nuevo.")
            if cobro.id_paciente_dentalink is None:
                return redirect(f"{reverse('cobros:nuevo')}?sin_dentalink=1")
            return redirect(f"{reverse('cobros:nuevo')}?paciente={cobro.id_paciente_dentalink}")
    extra = {
        "cancelar_abierto": True,
        "cancelar_errores": errores,
        "cancelar_motivo": motivo,
        "cancelar_detalle": detalle,
    }
    return _nuevo_asignado(request, pk, extra=extra, status=400)


def _fecha_comprobante_1(cobro):
    """Fecha en que se adjuntó el comprobante 1, según el historial. Sin evento (datos migrados): None."""
    if not cobro.comprobante:
        return None
    if cobro.migrado:  # 021/FR-014: el evento de alta es el de la migración, no el del cobro
        return cobro.fecha
    eventos = historial_de(cobro).filter(tipo__in=("alta", "cambio")).order_by("fecha", "id")
    for e in eventos:
        if any(c.get("campo") == "comprobante" and c.get("nuevo") for c in e.cambios):
            return e.fecha
    return None


def _lineas_cuenta(cobro):
    """Texto de «Copiar todo» de la cuenta destino (021/FR-009)."""
    cuenta = cobro.cuenta
    if not cuenta:
        return []
    lineas = [f"Monto: {pesos(cobro.monto)}"]
    for etiqueta, valor in (
        ("Titular", cuenta.titular),
        ("CUIT o CUIL", cuenta.titular_cuit),
        ("Alias", cuenta.alias),
        ("CBU o CVU", cuenta.cbu),
    ):
        if valor:
            lineas.append(f"{etiqueta}: {valor}")
    return lineas


def _contexto_detalle(request, cobro):
    admin = request.user.role == roles.ADMIN
    pendiente = cobro.estado == Cobro.Estado.PENDIENTE
    transferencia_finalizada = (
        admin
        and cobro.metodo == Cobro.Metodo.TRANSFERENCIA
        and cobro.estado == Cobro.Estado.FINALIZADO
    )
    contexto = {
        "cobro": cobro,
        "lineas_cuenta": _lineas_cuenta(cobro),
        "items": cobro.items.order_by("numero"),
        # 006/FR-032: el operador no ve cuenta ni destinatario de un cobro POS
        # 021/FR-008: el operador tampoco la ve en un cobro Cancelado ni Finalizado (016/FR-028)
        "ver_cuenta": admin or (cobro.metodo == Cobro.Metodo.TRANSFERENCIA and pendiente),
        "es_admin": admin,
        "pendiente_transferencia": pendiente and cobro.metodo == Cobro.Metodo.TRANSFERENCIA,
        "puede_adjuntar": pendiente or transferencia_finalizada,
        # 018/FR-015: el administrador en todo estado; el operador (cobro propio visible) sin Cancelado.
        "puede_observar": admin or cobro.estado != Cobro.Estado.CANCELADO,
        "puede_reabrir": transferencia_finalizada,
        "otros_comprobantes": cobro.otros_comprobantes.order_by("pk"),
        "fecha_comprobante_1": _fecha_comprobante_1(cobro),
        "cantidad_comprobantes": bool(cobro.comprobante) + cobro.otros_comprobantes.count(),
        "puede_completar": cobro.faltan_items and cobro.estado != Cobro.Estado.CANCELADO,
        # Con factura emitida cancela solo el administrador (decisión de Martin, 2026-10-22).
        "puede_cancelar": admin
        and cobro.estado != Cobro.Estado.CANCELADO
        or (pendiente and not factura_emitida(cobro)),
        "motivos": Cobro.MotivoCancelacion.choices,
    }
    contexto["cantidad_mas_acciones"] = contexto["puede_reabrir"] + bool(contexto["puede_cancelar"])
    if admin:  # 014/FR-012: la factura es del administrador
        contexto["comprobantes_fiscales"] = cobro.comprobantes_fiscales.order_by("pk")
        contexto["factura_emitida"] = factura_emitida(cobro)
        contexto["sin_factura_vigente"] = sin_factura_vigente(cobro)
    return contexto


@puede_cobrar
def detalle(request, pk):
    cobro = get_object_or_404(_visibles(request).select_related("cuenta", "terminal"), pk=pk)
    return render(request, "cobros/detalle.html", _contexto_detalle(request, cobro))


def _nota_credito(request):
    """La respuesta de la pregunta de 014: `si` es True, `no` es False, otro valor es None."""
    return {"si": True, "no": False}.get(request.POST.get("nota_credito"))


def _accion(request, pk, funcion, exito):
    cobro = get_object_or_404(_visibles(request), pk=pk)
    try:
        funcion(cobro)
    except ValidationError as e:
        _log(request, e)
        messages.error(request, " ".join(e.messages))
    else:
        messages.success(request, exito)
    return redirect("cobros:detalle", pk=cobro.pk)


def _observaciones_validas(request):
    """020/FR-007: el límite de 1.000 se valida antes del servicio. Texto o None si no es válido."""
    form = ObservacionesForm(request.POST)
    if form.is_valid():
        return form.cleaned_data["observaciones"]
    messages.error(request, " ".join(form.errors["observaciones"]))
    return None


@puede_cobrar
@require_POST
def adjuntar(request, pk):
    observaciones = None
    if "observaciones" in request.POST:
        observaciones = _observaciones_validas(request)
        if observaciones is None:
            return redirect("cobros:detalle", pk=pk)
    return _accion(
        request,
        pk,
        lambda c: servicios.adjuntar_comprobante(
            c,
            request.FILES.get("comprobante"),
            usuario=request.user,
            estado_visto=request.POST.get("estado_visto"),
            observaciones=observaciones,
        ),
        "Comprobante guardado.",
    )


@puede_cobrar
@require_POST
def observaciones(request, pk):
    texto = _observaciones_validas(request)
    if texto is None:
        return redirect("cobros:detalle", pk=pk)
    return _accion(
        request,
        pk,
        lambda c: servicios.cambiar_observaciones(c, usuario=request.user, observaciones=texto),
        "Observaciones guardadas.",
    )


@solo_administrador
@require_POST
def reabrir(request, pk):
    return _accion(
        request,
        pk,
        lambda c: servicios.reabrir_cobro(
            c,
            usuario=request.user,
            motivo=request.POST.get("motivo", ""),
            estado_visto=request.POST.get("estado_visto", ""),
        ),
        "Cobro reabierto.",
    )


@solo_administrador
def comprobante_otro(request, pk):
    otro = get_object_or_404(ComprobanteCobro, pk=pk)
    return servir(request, otro.archivo)


@puede_cobrar
@require_POST
def cancelar(request, pk):
    cobro = get_object_or_404(_visibles(request).select_related("cuenta", "terminal"), pk=pk)
    motivo = request.POST.get("motivo", "")
    detalle = request.POST.get("detalle", "").strip()
    errores = {}
    if not motivo.strip():
        errores["motivo"] = "Elegí un motivo de cancelación."
    elif motivo == Cobro.MotivoCancelacion.OTRO and not detalle:
        errores["detalle"] = "Escribí el detalle del motivo."
    if (
        request.user.role
        == roles.ADMIN  # el operador no cancela con factura: lo rechaza el servicio
        and factura_emitida(cobro)
        and _nota_credito(request) is None
    ):  # 021/FR-040
        errores["nota_credito"] = "Elegí Sí o No."
    if errores:  # FR-006: vuelve al detalle con «Más acciones» abierto y el error bajo el campo
        contexto = _contexto_detalle(request, cobro)
        contexto.update(
            cancelar_abierto=True,
            cancelar_errores=errores,
            cancelar_motivo=motivo,
            cancelar_detalle=detalle,
        )
        return render(request, "cobros/detalle.html", contexto, status=400)
    return _accion(
        request,
        pk,
        lambda c: servicios.cancelar_cobro(
            c,
            usuario=request.user,
            motivo=motivo,
            detalle=detalle,
            estado_visto=request.POST.get("estado_visto", ""),
            nota_credito=_nota_credito(request),
        ),
        "Cobro cancelado.",
    )


@puede_cobrar
def items(request, pk):
    cobro = get_object_or_404(_visibles(request), pk=pk, faltan_items=True)
    id_paciente = request.GET.get("paciente", "")
    if not id_paciente.isdigit():
        return redirect(f"{reverse('dentalink:pacientes')}?cobro={cobro.pk}")
    try:
        prestaciones = servicios.prestaciones_para_cobrar(int(id_paciente))
    except DentalinkNoDisponible as e:
        messages.warning(request, str(e))
        return render(request, "cobros/items.html", {"cobro": cobro, "caido": True})
    form = CobroForm(request.POST or None, prestaciones=prestaciones, metodos_pos_terminales={})
    del form.fields["metodo"]  # el método ya está elegido
    if request.method == "POST" and form.is_valid():
        try:
            servicios.completar_items(
                cobro, usuario=request.user, id_paciente=int(id_paciente), items=form.items()
            )
        except (ValidationError, DentalinkNoDisponible) as e:
            _log(request, e)
            form.add_error(None, e.messages if isinstance(e, ValidationError) else str(e))
        else:
            messages.success(request, "Ítems guardados.")
            return redirect("cobros:detalle", pk=cobro.pk)
    return render(request, "cobros/items.html", {"cobro": cobro, "form": form})


@solo_administrador
def editar(request, pk):
    cobro = get_object_or_404(Cobro, pk=pk)
    if cobro.estado == Cobro.Estado.CANCELADO:  # FR-005
        messages.info(request, "Un cobro Cancelado solo acepta cambios en observaciones.")
        return redirect("cobros:detalle", pk=cobro.pk)
    prestaciones = []
    if cobro.id_paciente_dentalink:
        try:
            prestaciones = servicios.prestaciones_para_cobrar(
                cobro.id_paciente_dentalink, cobro=cobro
            )
        except DentalinkNoDisponible as e:
            messages.warning(request, str(e))
    form = EditarCobroForm(
        request.POST or None, request.FILES or None, cobro=cobro, prestaciones=prestaciones
    )
    if request.method == "POST" and form.is_valid():
        try:
            servicios.editar_cobro(
                cobro,
                usuario=request.user,
                estado_visto=form.cleaned_data["estado_visto"],
                items=form.items(),
                comprobante=form.cleaned_data["comprobante"],
                nota_credito=_nota_credito(request),
                **form.cambios(),
            )
        except (ValidationError, DentalinkNoDisponible) as e:
            _log(request, e)
            form.add_error(None, e.messages if isinstance(e, ValidationError) else str(e))
        else:
            messages.success(request, "Cobro actualizado.")
            return redirect("cobros:detalle", pk=cobro.pk)
    return render(
        request,
        "cobros/editar.html",
        {"cobro": cobro, "form": form, "factura_emitida": factura_emitida(cobro)},
    )


def _next_seguro(request, por_defecto):
    destino = request.POST.get("next", "")
    ok = url_has_allowed_host_and_scheme(
        destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return destino if ok else por_defecto


def _con_seleccion(destino, pks):
    """`destino` con un `pk` repetido por cobro elegido (la tabla los marca)."""
    partes = urlsplit(destino)
    query = [(k, v) for k, v in parse_qsl(partes.query) if k != "pk"] + [("pk", p) for p in pks]
    return urlunsplit(partes._replace(query=urlencode(query)))


@solo_administrador
@require_POST
def revisado(request, pk):
    cobro = get_object_or_404(Cobro, pk=pk)
    marcar = request.POST.get("revisado") != "0"
    try:
        servicios.marcar_revisado(
            cobro,
            usuario=request.user,
            revisado=marcar,
            comentario=request.POST.get("comentario", ""),
        )
    except ValidationError as e:
        _log(request, e)
        messages.error(request, " ".join(e.messages))
    else:
        messages.success(request, "Cobro marcado como revisado." if marcar else "Revisión quitada.")
    destino = _next_seguro(request, reverse("cobros:detalle", args=[cobro.pk]))
    if request.headers.get("HX-Request") == "true":
        base = Cobro.objects.select_related("cuenta", "creado_por", "destinatario")
        fila = _anotadas(base.prefetch_related("items")).get(pk=cobro.pk)
        _decorar(fila)
        # 018/FR-031: el aviso viaja en esta respuesta y no queda guardado para la página siguiente.
        return render(
            request,
            "cobros/_fila.html",
            {
                "c": fila,
                "es_admin": True,
                "next_url": request.POST.get("next", ""),
                "avisos_oob": list(messages.get_messages(request)),
            },
        )
    return redirect(destino)


@solo_administrador
@require_POST
def cancelar_lote(request):
    destino = _next_seguro(request, reverse("cobros:lista"))
    pks = [int(p) for p in request.POST.getlist("pk") if p.isdigit()]
    if not pks:
        messages.error(request, "Elegí al menos un cobro.")
        return redirect(destino)
    try:
        cancelados, omitidos = servicios.cancelar_cobros(
            pks,
            usuario=request.user,
            motivo=request.POST.get("motivo", ""),
            detalle=request.POST.get("detalle", ""),
        )
    except ValidationError as e:
        _log(request, e)
        messages.error(request, " ".join(e.messages))
        return redirect(_con_seleccion(destino, pks))  # FR-021: la selección vuelve en la URL
    messages.success(request, f"Cancelados: {len(cancelados)}. Omitidos: {len(omitidos)}.")
    if omitidos:
        numeros = ", ".join(f"N.º {n}" for n in omitidos)
        messages.warning(request, f"Cobros omitidos: {numeros}.")
    return redirect(destino)


@solo_administrador
@require_POST
def revisado_lote(request):
    destino = _next_seguro(request, reverse("cobros:lista"))
    pks = [int(p) for p in request.POST.getlist("pk") if p.isdigit()]
    if not pks:
        messages.error(request, "Elegí al menos un cobro.")
        return redirect(destino)
    marcados, sin_comentario = servicios.marcar_revisados(pks, usuario=request.user)
    messages.success(request, f"Revisados: {len(marcados)}.")
    if sin_comentario:
        n = len(sin_comentario)
        numeros = ", ".join(f"N.º {p}" for p in sin_comentario)
        sujeto = (
            "1 cobro con excedente necesita" if n == 1 else f"{n} cobros con excedente necesitan"
        )
        messages.warning(request, f"{sujeto} comentario: {numeros}.")
    return redirect(destino)


@puede_cobrar
def comprobante(request, pk):
    # 021/FR-012 (D-15): el operador abre el de un cobro que puede ver; fuera del filtro, 404.
    cobro = get_object_or_404(_visibles(request), pk=pk)
    if not cobro.comprobante:
        raise Http404
    return servir(request, cobro.comprobante)


def _mes(texto):
    try:
        mes = datetime.strptime(texto, "%Y-%m").date()
    except (TypeError, ValueError):
        return servicios.mes_propuesto()
    return mes if 2000 <= mes.year <= 2100 else servicios.mes_propuesto()


TITULOS_PAGOS = [
    "Número", "Mes", "Fecha de carga", "Destinatario", "Monto", "Método", "Cuenta", "Estado",
    "Observaciones",
]  # fmt: skip


@solo_administrador
def complementarios(request):
    formato = request.GET.get("formato")
    if formato not in (None, "csv", "xlsx"):
        return HttpResponseBadRequest("Formato inválido.")
    mes_vacio = "mes" in request.GET and not request.GET["mes"]
    mes = _mes(request.GET.get("mes"))
    con_falta = request.GET.get("con_falta") == "1"
    pagos = (
        PagoComplementario.objects.filter(mes=mes)
        .select_related("destinatario", "cuenta")
        .order_by("-fecha_alta")
    )
    if formato:  # FR-029: sin mes no hay descarga (FR-042: los botones salen deshabilitados)
        if mes_vacio:
            return HttpResponseBadRequest("Elegí el mes.")
        filas = (
            [p.pk, p.mes, p.fecha_alta, p.destinatario.nombre, p.monto, p.get_metodo_display(),
             str(p.cuenta) if p.cuenta else "", p.get_estado_display(), p.observaciones]
            for p in pagos
        )  # fmt: skip
        return descarga.respuesta(
            "pagos-complementarios",
            TITULOS_PAGOS,
            filas,
            formato,
            nombre=f"pagos-complementarios-{mes:%Y-%m}",
        )
    filas = servicios.resumen_mes(mes, con_falta=con_falta)
    for f in filas:
        f.demas = -f.falta  # FR-026: "Recibió de más $X"
    contexto = {
        "mes": mes,
        "mes_vacio": mes_vacio,
        "con_falta": con_falta,
        "filas": filas,
        "pagos": pagos,
    }
    return render(request, "cobros/complementarios.html", contexto)


def _error_pago(request, form, e):
    _log(request, e)
    form.add_error(None, e.messages if isinstance(e, ValidationError) else str(e))


@solo_administrador
def complementario_nuevo(request):
    contexto_falta = None
    if request.method == "POST":
        form = PagoComplementarioForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                pago = servicios.registrar_pago_complementario(
                    usuario=request.user, **form.cleaned_data
                )
            except ValidationError as e:
                _error_pago(request, form, e)
            else:
                messages.success(request, "Pago complementario registrado.")
                return redirect("cobros:complementario_detalle", pk=pago.pk)
    else:
        mes = _mes(request.GET.get("mes"))
        inicial = request.GET.get("destinatario", "")
        valores = {"mes": mes, "destinatario": inicial if inicial.isdigit() else None}
        # FR-014: desde «Registrar pago» de una fila de «Falta del mes», el monto trae la falta.
        elegido = (
            Destinatario.objects.visibles().filter(pk=inicial).first()
            if inicial.isdigit()
            else None
        )
        if elegido:
            falta = servicios.resumen_mes(
                mes, destinatarios=Destinatario.objects.filter(pk=elegido.pk)
            )[0].falta
            if falta >= 0:
                contexto_falta = falta
            if falta > 0:
                valores["monto"] = falta
        form = PagoComplementarioForm(initial=valores)
    return render(
        request,
        "cobros/complementario_form.html",
        {"form": form, "pago": None, "falta": contexto_falta},
    )


@solo_administrador
def complementario_detalle(request, pk):
    pago = get_object_or_404(
        PagoComplementario.objects.select_related("destinatario", "cuenta", "creado_por"), pk=pk
    )
    from sistema.migracion.servicios import ORIGEN  # FR-014: el alta la creó la migración

    contexto = {
        "pago": pago,
        "migrado": historial_de(pago).filter(tipo="alta", origen=ORIGEN).exists(),
        "registrado": pago.estado == PagoComplementario.Estado.REGISTRADO,
        "form": AnularPagoForm(),
    }
    return render(request, "cobros/complementario_detalle.html", contexto)


@solo_administrador
def complementario_editar(request, pk):
    pago = get_object_or_404(PagoComplementario, pk=pk)
    if pago.estado == PagoComplementario.Estado.ANULADO:
        messages.error(request, "Un pago complementario Anulado no se edita.")
        return redirect("cobros:complementario_detalle", pk=pago.pk)
    if request.method == "POST":
        form = PagoComplementarioForm(request.POST, request.FILES, instance=pago)
        if form.is_valid():
            datos = dict(form.cleaned_data)
            comprobante = datos.pop("comprobante")
            try:
                servicios.editar_pago_complementario(
                    pago, usuario=request.user, comprobante=comprobante, **datos
                )
            except ValidationError as e:
                _error_pago(request, form, e)
            else:
                messages.success(request, "Pago complementario actualizado.")
                return redirect("cobros:complementario_detalle", pk=pago.pk)
    else:
        form = PagoComplementarioForm(
            instance=pago,
            initial={
                "destinatario": pago.destinatario_id,
                "mes": pago.mes,
                "monto": pago.monto,
                "metodo": pago.metodo,
                "cuenta": pago.cuenta_id,
                "observaciones": pago.observaciones,
            },
        )
    return render(request, "cobros/complementario_form.html", {"form": form, "pago": pago})


@solo_administrador
@require_POST
def complementario_anular(request, pk):
    pago = get_object_or_404(PagoComplementario, pk=pk)
    form = AnularPagoForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError("Elegí un motivo.")
        servicios.anular_pago_complementario(pago, usuario=request.user, **form.cleaned_data)
    except ValidationError as e:
        _log(request, e)
        messages.error(request, " ".join(e.messages))
    else:
        messages.success(request, "Pago complementario anulado.")
    return redirect("cobros:complementario_detalle", pk=pago.pk)


@solo_administrador
def complementario_comprobante(request, pk):
    pago = get_object_or_404(PagoComplementario, pk=pk)
    if not pago.comprobante:
        raise Http404
    return servir(request, pago.comprobante)
