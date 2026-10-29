from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from sistema.auditoria.servicios import foto, registrar_alta, registrar_cambio

from . import roles
from .models import Perfil

User = get_user_model()
ULTIMO_ADMIN = "Tiene que quedar al menos un administrador activo."
CAMPOS_EDITABLES = {"first_name", "last_name", "role", "observaciones"}
CAMPOS_PERFIL = (
    "observaciones",
    "id_profesional_dentalink",
    "nombre_profesional_dentalink",
    "fecha_activacion",
    "fecha_desactivacion",
)


def usuarios_visibles():
    """Usuarios para listas y filtros. Sin el usuario «para completar pagos» de la migración: está
    inactivo y es el único sin `Perfil` (021/FR-052). Sus eventos siguen en la auditoría."""
    return User.objects.exclude(perfil__isnull=True, is_active=False)


def _perfil(objetivo):
    return Perfil.objects.select_for_update().get_or_create(usuario=objetivo)[0]


def _foto_usuario(objetivo, perfil):
    return foto(objetivo) | {c: getattr(perfil, c) for c in CAMPOS_PERFIL}


def _bloquear_admins():
    """Bloquea las filas de administradores activos: dos pedidos a la vez se atienden en fila."""
    return list(User.objects.select_for_update().filter(role=roles.ADMIN, is_active=True))


def _validar_rol(role):
    if role not in roles.ETIQUETAS:
        raise ValidationError("Rol inválido.")


def crear_usuario(*, usuario, username, password, role, first_name="", last_name=""):
    _validar_rol(role)
    with transaction.atomic():
        nuevo = User(
            username=username,
            role=role,
            first_name=first_name,
            last_name=last_name,
            is_superuser=False,
            is_staff=False,
        )
        nuevo.set_password(password)
        try:
            nuevo.full_clean()
        except ValidationError as e:
            if "username" in e.message_dict:
                raise ValidationError("Ya existe un usuario con ese nombre.") from e
            raise
        nuevo.save()
        perfil = Perfil.objects.create(usuario=nuevo, fecha_activacion=timezone.now())
        registrar_alta(
            nuevo,
            usuario=usuario,
            extra={c: getattr(perfil, c) for c in CAMPOS_PERFIL},
        )
        return nuevo


def _es_ultimo_admin(objetivo):
    admins = _bloquear_admins()
    return [a.pk for a in admins] == [objetivo.pk]


def editar_usuario(objetivo, *, usuario, **datos):
    if set(datos) - CAMPOS_EDITABLES:
        raise ValidationError("Solo se editan nombre, apellido, rol y observaciones.")
    if "role" in datos:
        _validar_rol(datos["role"])
    with transaction.atomic():
        objetivo = User.objects.select_for_update().get(pk=objetivo.pk)
        perfil = _perfil(objetivo)
        antes = _foto_usuario(objetivo, perfil)
        if (
            objetivo.role == roles.ADMIN
            and objetivo.is_active
            and datos.get("role", roles.ADMIN) != roles.ADMIN
            and _es_ultimo_admin(objetivo)
        ):
            raise ValidationError(ULTIMO_ADMIN)
        for campo, valor in datos.items():
            setattr(perfil if campo == "observaciones" else objetivo, campo, valor)
        objetivo.full_clean(exclude=["password"])
        if _foto_usuario(objetivo, perfil) == antes:
            return objetivo
        objetivo.save()
        perfil.save()
        registrar_cambio(objetivo, antes, usuario=usuario, despues=_foto_usuario(objetivo, perfil))
        return objetivo


def cambiar_activo(objetivo, activo, *, usuario):
    with transaction.atomic():
        objetivo = User.objects.select_for_update().get(pk=objetivo.pk)
        perfil = _perfil(objetivo)
        antes = _foto_usuario(objetivo, perfil)
        if (
            not activo
            and objetivo.role == roles.ADMIN
            and objetivo.is_active
            and _es_ultimo_admin(objetivo)
        ):
            raise ValidationError(ULTIMO_ADMIN)
        if objetivo.is_active == bool(activo):
            return objetivo
        if activo:
            perfil.fecha_activacion = timezone.now()
        else:
            perfil.fecha_desactivacion = timezone.now()
        objetivo.is_active = bool(activo)
        objetivo.save(update_fields=["is_active"])
        perfil.save()
        registrar_cambio(objetivo, antes, usuario=usuario, despues=_foto_usuario(objetivo, perfil))
        return objetivo


def poner_contrasena(objetivo, password, *, usuario):
    with transaction.atomic():
        objetivo = User.objects.select_for_update().get(pk=objetivo.pk)
        objetivo.set_password(password)
        objetivo.save(update_fields=["password"])
        _perfil(objetivo).save()
        registrar_cambio(objetivo, {}, despues={}, usuario=usuario, detalle="Contraseña cambiada")
        return objetivo


def vincular_profesional(
    objetivo, *, usuario, id_profesional_dentalink, nombre_profesional_dentalink
):
    """id=None y nombre "" desvinculan."""
    if objetivo.role != roles.PROFESSIONAL:
        raise ValidationError("Solo se vincula un usuario Profesional.")
    with transaction.atomic():
        perfil = _perfil(objetivo)
        antes = _foto_usuario(objetivo, perfil)
        perfil.id_profesional_dentalink = id_profesional_dentalink
        perfil.nombre_profesional_dentalink = nombre_profesional_dentalink.strip()
        perfil.full_clean()
        if _foto_usuario(objetivo, perfil) == antes:
            return perfil
        perfil.save()
        registrar_cambio(objetivo, antes, usuario=usuario, despues=_foto_usuario(objetivo, perfil))
        return perfil
