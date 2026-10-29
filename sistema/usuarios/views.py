from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import localtime
from django.views.decorators.http import require_POST

from sistema.avisos.models import DireccionAviso
from sistema.arca.models import ComprobanteFiscal
from sistema.cobros.models import Cobro, ItemCobro
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import DURACION_BLOQUEO, _REINICIOS, desbloquear_usuario
from sistema.dentalink.models import Profesional
from sistema.destinatarios.models import Destinatario
from sistema.core.tiempo import ARG, inicio_dia_arg, rango_mes_arg
from sistema.core.permisos import (
    con_rol,
    filtro_cobros_visibles,
    puede_cobrar,
    solo_administrador,
    solo_profesional,
)

from sistema.terminales.servicios import cuentas_logueadas
from sistema.reportes.servicios import (
    cobrado,
    items_del_mes,
    items_por_paciente,
    leer_mes,
    mes_corriente,
    reporte_destinatario,
)

from . import roles
from .forms import ClaveForm, UsuarioAltaForm, UsuarioEdicionForm
from .models import Perfil
from .servicios import (
    cambiar_activo,
    crear_usuario,
    editar_usuario,
    poner_contrasena,
    usuarios_visibles,
    vincular_profesional,
)

User = get_user_model()


@con_rol(*roles.ETIQUETAS)
def inicio(request):
    return redirect(roles.ROL_A_PANTALLA[request.user.role])


def _pendientes():
    """Contadores de solo lectura del panel (016/FR-030). Cada uno enlaza a un filtro existente."""
    errores = ItemCobro.objects.filter(estado_envio=ItemCobro.EstadoEnvio.ERROR).count()
    excedentes = Cobro.objects.filter(excedente=True, revisado=False).count()
    facturas = (
        ComprobanteFiscal.objects.filter(estado=ComprobanteFiscal.Estado.ERROR)
        .values("cobro")
        .distinct()
        .count()
    )
    revisar = ItemCobro.objects.filter(estado_envio=ItemCobro.EstadoEnvio.REVISAR).count()
    return [
        ("Ítems en error de Dentalink", errores, reverse("dentalink:envios") + "?estado=error"),
        (
            "Excedentes sin revisar",
            excedentes,
            reverse("cobros:lista") + "?excedente_sin_revisar=on",
        ),
        ("Facturas con error", facturas, reverse("cobros:lista") + "?factura_con_error=on"),
        (
            "Ítems para revisar en Dentalink",
            revisar,
            reverse("dentalink:envios") + "?estado=revisar",
        ),
    ]


@solo_administrador
def panel(request):
    ahora = timezone.now()
    hoy = localtime(ahora, ARG).date()
    desde_mes, hasta_mes = rango_mes_arg(ahora)
    lista = reverse("cobros:lista") + "?estado=finalizado&desde={}&hasta={}"
    return render(
        request,
        "usuarios/panel.html",
        {
            "pendientes": _pendientes(),
            "cobrado_hoy": cobrado(inicio_dia_arg(hoy), inicio_dia_arg(hoy + timedelta(days=1))),
            "cobrado_mes": cobrado(desde_mes, hasta_mes),
            "href_hoy": lista.format(hoy.isoformat(), hoy.isoformat()),
            "href_mes": lista.format(
                desde_mes.date().isoformat(), (hasta_mes - timedelta(days=1)).date().isoformat()
            ),
            "pos": cuentas_logueadas(),
            "sin_direcciones": not DireccionAviso.objects.exists(),
        },
    )


@puede_cobrar
def operador(request):
    pendientes = Cobro.objects.filter(
        filtro_cobros_visibles(request.user), estado="pendiente"
    ).count()
    return render(request, "usuarios/operador.html", {"pendientes": pendientes})


@solo_profesional
def profesional(request):
    perfil = Perfil.objects.filter(usuario=request.user).first()
    id_profesional = perfil.id_profesional_dentalink if perfil else None
    mes = leer_mes(request.GET.get("mes"))
    q = request.GET.get("q", "").strip()
    con_vinculo = bool(id_profesional)
    if not con_vinculo:
        items, total = [], Decimal("0.00")
    elif q:
        items, total = items_por_paciente(id_profesional, q)
    else:
        items, total = items_del_mes(id_profesional, mes)
    destinatario = Destinatario.objects.filter(usuario_profesional=request.user).first()
    contexto = {
        "perfil": perfil,
        "pagina": Paginator(items, 50).get_page(request.GET.get("page")),
        "destinatario": destinatario,
        "q": q,
        "mes": mes,
        "corriente": mes_corriente(),
        "total": total,
        "con_vinculo": con_vinculo,
        "reporte": reporte_destinatario(destinatario, mes) if destinatario else None,
    }
    return render(request, "usuarios/profesional.html", contexto)


@solo_administrador
def lista(request):
    qs = usuarios_visibles().select_related("perfil", "destinatario").order_by("username")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(username__unaccent__icontains=q)
            | Q(first_name__unaccent__icontains=q)
            | Q(last_name__unaccent__icontains=q)
        )
    rol = request.GET.get("rol", "")
    if rol in roles.ETIQUETAS:
        qs = qs.filter(role=rol)
    else:
        rol = ""
    usuarios = list(qs)
    # Una sola consulta para todos los bloqueos (021/FR-062); mismo criterio que esta_bloqueado().
    ultimos = {
        e.entidad_id: e
        for e in Evento.objects.filter(
            entidad=User._meta.label_lower,
            entidad_id__in=[u.pk for u in usuarios],
            tipo__in=_REINICIOS,
        )
        .order_by("entidad_id", "-fecha", "-id")
        .distinct("entidad_id")
    }
    ahora = timezone.now()
    for u in usuarios:
        u.rol_etiqueta = roles.ETIQUETAS.get(u.role, u.role)
        ultimo = ultimos.get(u.pk)
        u.bloqueado = bool(
            ultimo
            and ultimo.tipo == Evento.Tipo.BLOQUEO
            and ahora < ultimo.fecha + DURACION_BLOQUEO
        )
    contexto = {"usuarios": usuarios, "q": q, "rol": rol, "roles": roles.ETIQUETAS.items()}
    return render(request, "usuarios/lista.html", contexto)


@solo_administrador
def nuevo(request):
    form = UsuarioAltaForm(request.POST or None)
    if form.is_valid():
        d = form.cleaned_data
        try:
            crear_usuario(
                usuario=request.user,
                username=d["username"],
                password=d["password1"],
                role=d["role"],
                first_name=d["first_name"],
                last_name=d["last_name"],
            )
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Usuario creado.")
            return redirect("usuarios:lista")
    return render(request, "usuarios/form.html", {"form": form, "titulo": "Nuevo usuario"})


@solo_administrador
def editar(request, pk):
    objetivo = get_object_or_404(User, pk=pk)
    inicial = {f: getattr(objetivo, f) for f in ("first_name", "last_name", "role")}
    perfil = Perfil.objects.filter(usuario=objetivo).first()
    if perfil:
        inicial["observaciones"] = perfil.observaciones
        inicial["profesional_dentalink"] = Profesional.objects.filter(
            id_dentalink=perfil.id_profesional_dentalink, vigente=True
        ).first()
    form = UsuarioEdicionForm(request.POST or None, initial=inicial, rol=objetivo.role)
    if form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                objetivo = editar_usuario(
                    objetivo,
                    usuario=request.user,
                    first_name=d["first_name"],
                    last_name=d["last_name"],
                    role=d["role"],
                    observaciones=d["observaciones"],
                )
                # Solo si cambió: un vínculo fuera del catálogo vigente (migrado) no se borra solo
                if (
                    "profesional_dentalink" in form.changed_data
                    and objetivo.role == roles.PROFESSIONAL
                ):
                    vincular_profesional(
                        objetivo,
                        usuario=request.user,
                        id_profesional_dentalink=d["id_profesional_dentalink"],
                        nombre_profesional_dentalink=d["nombre_profesional_dentalink"],
                    )
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Usuario actualizado.")
            return redirect("usuarios:lista")
    titulo = f"Editar {objetivo.username}"
    return render(request, "usuarios/form.html", {"form": form, "titulo": titulo, "editando": True})


@solo_administrador
@require_POST
def activo(request, pk):
    objetivo = get_object_or_404(User, pk=pk)
    try:
        cambiar_activo(objetivo, request.POST.get("activo") == "1", usuario=request.user)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    else:
        messages.success(request, "Usuario actualizado.")
    return redirect("usuarios:lista")


@solo_administrador
def contrasena(request, pk):
    objetivo = get_object_or_404(User, pk=pk)
    form = ClaveForm(objetivo, request.POST or None)
    if form.is_valid():
        poner_contrasena(objetivo, form.cleaned_data["new_password1"], usuario=request.user)
        messages.success(request, "Contraseña actualizada.")
        return redirect("usuarios:lista")
    return render(request, "usuarios/contrasena.html", {"form": form, "objetivo": objetivo})


@solo_administrador
@require_POST
def desbloquear(request, pk):
    objetivo = get_object_or_404(User, pk=pk)
    try:
        desbloquear_usuario(objetivo, usuario=request.user)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    else:
        messages.success(request, "Usuario desbloqueado.")
    return redirect("usuarios:lista")
