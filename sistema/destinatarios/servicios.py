import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from sistema.asignacion.servicios import fila, sincronizar_lista
from sistema.auditoria.servicios import foto, registrar_alta, registrar_cambio

from sistema.core.tiempo import rango_mes_arg, rango_semana_arg

from .models import Cuenta, Destinatario, Proveedor, TopeVigente

logger = logging.getLogger(__name__)


def _guardar(obj, campos=None):
    """full_clean + save. Si otro pedido guardó lo mismo a la vez, full_clean ve la otra fila.
    Con `campos`, el UPDATE solo lleva esos (`update_fields`)."""
    obj.full_clean()
    try:
        with transaction.atomic():
            obj.save(update_fields=campos)
    except IntegrityError:
        obj.full_clean()  # lanza el ValidationError con el mensaje exacto de la constraint
        raise
    return obj


def crear_destinatario(*, usuario, **datos):
    with transaction.atomic():
        destinatario = Destinatario(**datos)
        if destinatario.estado == Destinatario.Estado.ACTIVO:
            destinatario.fecha_activacion = timezone.now()
        elif destinatario.estado == Destinatario.Estado.ARCHIVADO:
            destinatario.fecha_archivado = timezone.now()
        _guardar(destinatario)
        registrar_alta(destinatario, usuario=usuario)
        return destinatario


def editar_destinatario(destinatario, *, usuario, **datos):
    if "estado" in datos:
        raise ValidationError("El estado se cambia con cambiar_estado_destinatario.")
    with transaction.atomic():
        destinatario = Destinatario.objects.select_for_update().get(pk=destinatario.pk)
        antes = foto(destinatario)
        for campo, valor in datos.items():
            setattr(destinatario, campo, valor)
        if foto(destinatario) == antes:
            return destinatario
        _guardar(destinatario)
        registrar_cambio(destinatario, antes, usuario=usuario)
        return destinatario


def crear_cuenta(*, usuario, tipo, destinatario, **datos):
    if tipo == Cuenta.Tipo.TRANSFERENCIA:
        for campo in ("tope_semanal", "minimo_por_pago"):
            if datos.get(campo) is None:
                datos[campo] = Decimal("0.00")
    if tipo == Cuenta.Tipo.TERMINAL_POS and not datos.get("acumula_desde"):
        datos["acumula_desde"] = timezone.now()
    with transaction.atomic():
        fila()
        cuenta = Cuenta(tipo=tipo, destinatario=destinatario, **datos)
        if cuenta.estado == Cuenta.Estado.ACTIVA:
            cuenta.fecha_activacion = timezone.now()
        elif cuenta.estado == Cuenta.Estado.ARCHIVADA:
            cuenta.fecha_archivado = timezone.now()
        _guardar(cuenta)
        if tipo == Cuenta.Tipo.TRANSFERENCIA:
            for t in TopeVigente.Tipo.values:
                TopeVigente.objects.create(
                    cuenta=cuenta,
                    tipo=t,
                    monto=getattr(cuenta, f"tope_{t}"),
                    desde=cuenta.fecha_alta,
                )
        sincronizar_lista(usuario=usuario, sin_evento=cuenta)
        cuenta.refresh_from_db()
        registrar_alta(cuenta, usuario=usuario)
        return cuenta


def _aplicar_estado(obj, estado, activo, archivado):
    """Cambia estado y fechas (FR-013). Mismo estado: no cambia nada."""
    if obj.estado == estado:
        return obj
    obj.estado = estado
    if estado == activo:
        obj.fecha_activacion = timezone.now()
    elif estado == archivado:
        obj.fecha_archivado = timezone.now()
    return _guardar(obj)


def cambiar_estado_destinatario(destinatario, estado, *, usuario):
    from sistema.terminales.models import Sesion  # terminales importa destinatarios

    with transaction.atomic():
        fila()
        destinatario = Destinatario.objects.select_for_update().get(pk=destinatario.pk)
        if (
            estado != Destinatario.Estado.ACTIVO
            and Sesion.objects.filter(cuenta__destinatario=destinatario, fin__isnull=True).exists()
        ):
            raise ValidationError(
                "El destinatario tiene una cuenta con una sesión abierta en una terminal POS. "
                "Cerrá la sesión primero."
            )
        antes = foto(destinatario)
        _aplicar_estado(
            destinatario, estado, Destinatario.Estado.ACTIVO, Destinatario.Estado.ARCHIVADO
        )
        registrar_cambio(destinatario, antes, usuario=usuario)
        sincronizar_lista(usuario=usuario)
        return destinatario


def cambiar_estado_destinatarios(pks, estado, *, usuario):
    """Pasa a `estado` ("activo", "inactivo" o "archivado") a cada destinatario, en una transacción por uno.
    Saltea los ya Archivados y los que ya están en ese estado; los que fallan se omiten (P-21).
    Devuelve (cambiados, omitidos): listas de pk."""
    from sistema.usuarios import roles

    if getattr(usuario, "role", None) != roles.ADMIN:
        raise PermissionDenied
    if estado not in (
        Destinatario.Estado.ACTIVO,
        Destinatario.Estado.INACTIVO,
        Destinatario.Estado.ARCHIVADO,
    ):
        raise ValidationError("Estado inválido.")
    cambiados, omitidos = [], []
    for d in Destinatario.objects.filter(pk__in=pks).order_by("pk"):
        if d.estado in (Destinatario.Estado.ARCHIVADO, estado):
            omitidos.append(d.pk)
            continue
        try:
            with transaction.atomic():
                cambiar_estado_destinatario(d, estado, usuario=usuario)
        except ValidationError:
            omitidos.append(d.pk)
        except Exception:
            logger.exception("Error inesperado al cambiar estado en lote: destinatario=%s", d.pk)
            omitidos.append(d.pk)  # motivo: "Error inesperado"
        else:
            cambiados.append(d.pk)
    return cambiados, omitidos


def cambiar_estado_cuenta(cuenta, estado, *, usuario, origen=""):
    with transaction.atomic():
        fila()
        cuenta = Cuenta.objects.select_for_update().get(pk=cuenta.pk)
        if estado != Cuenta.Estado.ACTIVA and cuenta.sesiones.filter(fin__isnull=True).exists():
            raise ValidationError(
                "La cuenta tiene una sesión abierta en una terminal POS. Cerrá la sesión primero."
            )
        antes = foto(cuenta)
        if estado != Cuenta.Estado.ACTIVA:
            cuenta.posicion = None  # si no, falla cuenta_posicion_valida
        _aplicar_estado(cuenta, estado, Cuenta.Estado.ACTIVA, Cuenta.Estado.ARCHIVADA)
        sincronizar_lista(usuario=usuario, origen=origen, sin_evento=cuenta)
        cuenta.refresh_from_db()
        registrar_cambio(cuenta, antes, usuario=usuario, origen=origen)
        return cuenta


def desactivar_cuentas(destinatario, *, usuario) -> int:
    """Pasa a Inactiva todas las cuentas Activas. Si una falla, ninguna cambia (FR-012)."""
    with transaction.atomic():
        fila()
        activas = list(destinatario.cuentas.select_for_update().filter(estado=Cuenta.Estado.ACTIVA))
        for cuenta in activas:
            cambiar_estado_cuenta(cuenta, Cuenta.Estado.INACTIVA, usuario=usuario)
        return len(activas)


def editar_cuenta(cuenta, *, usuario, **datos):
    for campo in ("tipo", "proveedor", "estado", "acumula_desde"):
        if campo in datos:
            raise ValidationError(f"El campo {campo} no se cambia al editar la cuenta.")
    with transaction.atomic():
        fila()
        cuenta = Cuenta.objects.select_for_update().get(pk=cuenta.pk)
        antes = foto(cuenta)
        for campo, valor in datos.items():
            setattr(cuenta, campo, valor)
        if foto(cuenta) == antes:
            return cuenta
        # 006/FR-030: una cuenta en una sesión abierta no pasa a un destinatario no Activo.
        if (
            cuenta.destinatario_id != antes["destinatario_id"]
            and cuenta.sesiones.filter(fin__isnull=True).exists()
            and Destinatario.objects.get(pk=cuenta.destinatario_id).estado
            != Destinatario.Estado.ACTIVO
        ):
            raise ValidationError(
                "La cuenta tiene una sesión abierta en una terminal POS. "
                "El destinatario nuevo debe estar Activo."
            )
        _guardar(cuenta)
        if cuenta.tipo == Cuenta.Tipo.TRANSFERENCIA:
            ahora = timezone.now()
            for t in TopeVigente.Tipo.values:
                if foto(cuenta).get(f"tope_{t}") != antes.get(f"tope_{t}"):
                    # mismo instante que el registro anterior: lo reemplaza
                    TopeVigente.objects.update_or_create(
                        cuenta=cuenta,
                        tipo=t,
                        desde=ahora,
                        defaults={"monto": getattr(cuenta, f"tope_{t}")},
                    )
        sincronizar_lista(usuario=usuario, sin_evento=cuenta)
        cuenta.refresh_from_db()
        registrar_cambio(cuenta, antes, usuario=usuario)
        return cuenta


def crear_proveedor(*, usuario, nombre):
    with transaction.atomic():
        proveedor = _guardar(Proveedor(nombre=nombre.strip()))
        registrar_alta(proveedor, usuario=usuario)
        return proveedor


def tope_de(cuenta, tipo, momento):
    """Tope `tipo` ("mensual"/"semanal") que rigió en el período de `momento` (FR-029).
    Último registro con desde < fin del período; si ninguno, el más antiguo (como 012/FR-027).
    Sin historial (cuenta migrada sin registros): el valor actual."""
    rango = rango_mes_arg if tipo == "mensual" else rango_semana_arg
    _, hasta = rango(momento)
    # en Python: con prefetch_related("topes") no hay una consulta por cuenta
    topes = sorted((t for t in cuenta.topes.all() if t.tipo == tipo), key=lambda t: (t.desde, t.pk))
    vigentes = [t for t in topes if t.desde < hasta]
    t = vigentes[-1] if vigentes else (topes[0] if topes else None)
    return t.monto if t else getattr(cuenta, f"tope_{tipo}")


def esperado_mensual(cuenta, momento):
    """Esperado del mes de `momento` (021/FR-027). Si la cuenta tiene topes mensuales y el mes termina
    antes del primero (rango semiabierto), vale $0,00; si no, `tope_de` (que no cambia)."""
    _, hasta = rango_mes_arg(momento)
    topes = [t for t in cuenta.topes.all() if t.tipo == "mensual"]
    if topes and all(t.desde >= hasta for t in topes):
        return Decimal("0.00")
    return tope_de(cuenta, "mensual", momento)
