from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.utils import timezone
from django.utils.formats import number_format
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render

from sistema.cobros.models import Cobro
from sistema.core.tiempo import ARG
from sistema.core.permisos import con_rol, filtro_cobros_visibles, solo_administrador
from sistema.destinatarios.models import Destinatario
from sistema.usuarios import roles

from . import descarga, servicios
from .filtros import FiltroCobros


@con_rol(roles.ADMIN, roles.PROFESSIONAL)
def destinatario(request, pk):
    d = get_object_or_404(Destinatario, pk=pk)
    if request.user.role == roles.PROFESSIONAL and d.usuario_profesional_id != request.user.pk:
        raise Http404
    mes = servicios.leer_mes(request.GET.get("mes"))
    contexto = {
        "reporte": servicios.reporte_destinatario(d, mes),
        "mes": mes,
        "corriente": servicios.mes_corriente(),
    }
    return render(request, "reportes/destinatario.html", contexto)


@solo_administrador
def destinatarios_descargar(request):
    formato = request.GET.get("formato")
    if formato not in ("csv", "xlsx"):
        return HttpResponseBadRequest("Formato inválido.")
    mes = servicios.leer_mes(request.GET.get("mes"))
    filas = servicios.filas_destinatarios(mes, con_falta=request.GET.get("con_falta") == "1")
    return descarga.respuesta(
        "destinatarios",
        servicios.TITULOS_DESTINATARIOS,
        filas,
        formato,
        nombre=f"falta-del-mes-{mes:%Y-%m}",
    )


@solo_administrador
def cobros_descargar(request):
    formato, por = request.GET.get("formato"), request.GET.get("por")
    if formato == "zip" and not por:
        return _zip_del_filtro(request)
    if formato not in ("csv", "xlsx") or por not in ("cobro", "item"):
        return HttpResponseBadRequest("Parámetros inválidos.")
    cobros = FiltroCobros(
        request.GET, queryset=Cobro.objects.filter(filtro_cobros_visibles(request.user))
    ).qs
    return descargar_cobros(request, cobros, formato, por)


def _respuesta_zip(request, cobros):
    archivo = servicios.zip_comprobantes(cobros)
    if archivo is None:
        messages.warning(request, "No se encontraron comprobantes en los cobros elegidos.")
        return None
    fecha = timezone.localtime(timezone.now(), ARG).strftime("%d-%m-%Y")
    return FileResponse(archivo, as_attachment=True, filename=f"comprobantes-{fecha}.zip")


def _zip_del_filtro(request):
    cobros = FiltroCobros(
        request.GET, queryset=Cobro.objects.filter(filtro_cobros_visibles(request.user))
    ).qs
    filtro = request.GET.copy()
    filtro.pop("formato", None)
    destino = reverse("cobros:lista") + ("?" + filtro.urlencode() if filtro else "")
    cantidad = cobros.count()
    if cantidad > servicios.MAX_ZIP:
        messages.error(
            request,
            f"Hay {number_format(cantidad, use_l10n=True, force_grouping=True)} cobros con este "
            f"filtro. Elegí un rango con {number_format(servicios.MAX_ZIP, use_l10n=True, force_grouping=True)} o menos.",
        )
        return redirect(destino)
    return _respuesta_zip(request, cobros) or redirect(destino)


def descargar_cobros(request, cobros, formato, por):
    cobros = cobros.prefetch_related("otros_comprobantes")

    def url(c):
        """Un enlace por comprobante (el 1 y los demás), uno por línea (007/FR-052)."""
        if not c.comprobante:
            return ""
        enlaces = [reverse("cobros:comprobante", args=[c.pk])]
        enlaces += [
            reverse("cobros:comprobante_otro", args=[o.pk]) for o in c.otros_comprobantes.all()
        ]
        return "\n".join(request.build_absolute_uri(e) for e in enlaces)

    if por == "item":
        tipo, titulos = "cobros-por-item", servicios.TITULOS_ITEM
        filas = servicios.filas_por_item(cobros, url_comprobante=url)
    else:
        tipo, titulos = "cobros", servicios.TITULOS_COBRO
        filas = servicios.filas_por_cobro(cobros, url_comprobante=url)
    return descarga.respuesta(tipo, titulos, filas, formato)


@solo_administrador
@require_POST
def cobros_elegidos(request):
    pks = [p for p in request.POST.getlist("pk") if p.isdigit()]
    cobros = Cobro.objects.filter(filtro_cobros_visibles(request.user), pk__in=pks)
    if not cobros.exists():
        messages.error(request, "Elegí al menos un cobro.")
        return redirect("cobros:lista")
    accion = request.POST.get("accion")
    if accion == "descargar":
        formato, por = request.POST.get("formato"), request.POST.get("por")
        if formato not in ("csv", "xlsx") or por not in ("cobro", "item"):
            return HttpResponseBadRequest("Parámetros inválidos.")
        return descargar_cobros(request, cobros, formato, por)
    if accion == "comprobantes":
        return _respuesta_zip(request, cobros) or redirect("cobros:lista")
    if accion == "copiar":
        return render(
            request, "reportes/portapapeles.html", {"texto": servicios.texto_portapapeles(cobros)}
        )
    return HttpResponseBadRequest("Acción inválida.")
