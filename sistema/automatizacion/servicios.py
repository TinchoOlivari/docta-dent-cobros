import calendar
import logging
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from sistema.asignacion.servicios import fila, mover_cuenta
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import foto, registrar, registrar_alta, registrar_cambio
from sistema.core.tiempo import ARG, inicio_dia_arg
from sistema.destinatarios.models import Cuenta
from sistema.destinatarios.servicios import cambiar_estado_cuenta

from .models import Regla

logger = logging.getLogger(__name__)


def _guardar(regla):
    """full_clean + save. Si otro pedido guardó lo mismo a la vez, full_clean ve la otra fila."""
    regla.full_clean()
    try:
        with transaction.atomic():
            regla.save()
    except IntegrityError:
        regla.full_clean()  # lanza el ValidationError con el mensaje exacto de la constraint
        raise
    return regla


def crear_regla(*, usuario, **datos):
    with transaction.atomic():
        regla = Regla(**datos)
        if regla.estado == Regla.Estado.ACTIVA:
            regla.fecha_activacion = timezone.now()
        _guardar(regla)
        registrar_alta(regla, usuario=usuario)
        return regla


def editar_regla(regla, *, usuario, **datos):
    if "estado" in datos or "ultima_ejecucion" in datos:
        raise ValidationError("El estado se cambia con activar o desactivar.")
    with transaction.atomic():
        regla = Regla.objects.select_for_update().get(pk=regla.pk)
        antes = foto(regla)
        for campo, valor in datos.items():
            setattr(regla, campo, valor)
        if foto(regla) == antes:
            return regla
        _guardar(regla)
        registrar_cambio(regla, antes, usuario=usuario)
        return regla


def _frenar_choques(filas):
    """FR-043: si activar choca, el error nombra cada regla que choca y con cuál. Ninguna se activa."""
    clave = lambda r: (r.cuenta_id, r.disparador, r.dia, r.hora)  # noqa: E731
    ocupadas = {clave(r): r for r in Regla.objects.filter(estado=Regla.Estado.ACTIVA)}
    choques = []
    for regla in filas:
        if regla.estado == Regla.Estado.ACTIVA:
            continue
        otra = ocupadas.setdefault(clave(regla), regla)
        if otra is not regla:
            choques.append(
                f"«{regla.nombre}» choca con «{otra.nombre}» "
                "(misma cuenta, disparador, día y hora)."
            )
    if choques:
        raise ValidationError(" ".join(choques) + " No se activó ninguna.")


def cambiar_estado_reglas(reglas, estado, *, usuario):
    """Todo o nada: un ValidationError deshace todas. Devuelve cuántas cambiaron."""
    cambiadas = 0
    with transaction.atomic():
        filas = (
            Regla.objects.select_for_update().filter(pk__in=[r.pk for r in reglas]).order_by("pk")
        )
        filas = list(filas)
        if estado == Regla.Estado.ACTIVA:
            _frenar_choques(filas)
        for regla in filas:
            if regla.estado == estado:
                continue
            antes = foto(regla)
            regla.estado = estado
            if estado == Regla.Estado.ACTIVA:
                regla.fecha_activacion = timezone.now()
            _guardar(regla)
            registrar_cambio(regla, antes, usuario=usuario)
            cambiadas += 1
    return cambiadas


def destino(accion, k, n):
    """Posición final según la acción, con n cuentas en la lista (FR-015)."""
    if accion == Regla.Accion.PRIMERO:
        return 1
    if accion == Regla.Accion.ULTIMO:
        return n
    if accion == Regla.Accion.MEDIO:
        return (n + 1) // 2
    return min(k, n)


def _aplicar(regla, cuenta, origen):
    """Aplica la acción. Devuelve (cambios, detalle). ValidationError si no se puede."""
    if regla.accion in (Regla.Accion.ACTIVAR, Regla.Accion.DESACTIVAR):
        if cuenta.estado == Cuenta.Estado.ARCHIVADA:
            raise ValidationError("Una regla no saca una cuenta de Archivada.")
        activar = regla.accion == Regla.Accion.ACTIVAR
        nuevo = Cuenta.Estado.ACTIVA if activar else Cuenta.Estado.INACTIVA
        if cuenta.estado == nuevo:
            return [], "sin cambios."
        anterior = cuenta.get_estado_display()
        cambiar_estado_cuenta(cuenta, nuevo, usuario=None, origen=origen)
        cambios = [{"campo": "estado de la cuenta", "anterior": anterior, "nuevo": nuevo.label}]
        return cambios, "activada." if activar else "desactivada."
    if cuenta.posicion is None:
        raise ValidationError("La cuenta no está en la lista de prioridad.")
    n = Cuenta.objects.filter(posicion__isnull=False).count()
    k = destino(regla.accion, regla.posicion, n)
    pedida = regla.accion == Regla.Accion.POSICION and regla.posicion > n
    if k == cuenta.posicion:
        return [], (f"pedida {regla.posicion}, aplicada {k}." if pedida else "sin cambios.")
    antes = cuenta.posicion
    mover_cuenta(cuenta, k, usuario=None, origen=origen)
    cambios = [{"campo": "posición de la cuenta", "anterior": antes, "nuevo": k}]
    return cambios, (
        f"pedida {regla.posicion}, aplicada {k}." if pedida else f"movida a la posición {k}."
    )


def toca(regla, ahora):
    """Sin consultas (research R2). Todo en hora Argentina."""
    if regla.estado != Regla.Estado.ACTIVA:
        return False
    hoy = ahora.astimezone(ARG).date()
    if regla.disparador == Regla.Disparador.SEMANA:
        if regla.dia != hoy.isoweekday():
            return False
    elif min(regla.dia, calendar.monthrange(hoy.year, hoy.month)[1]) != hoy.day:
        return False
    programado = datetime.combine(hoy, regla.hora, tzinfo=ARG)
    if programado > ahora:
        return False
    ultima = regla.ultima_ejecucion
    if ultima is not None and ultima >= inicio_dia_arg(hoy):
        return False
    return regla.fecha_cambio <= programado  # FR-012


def ejecutar(regla_pk, ahora):
    """Ejecuta una regla (research R4). Devuelve el Evento o None si no toca."""
    with transaction.atomic():
        fila()
        regla = Regla.objects.select_for_update().get(pk=regla_pk)
        if not toca(regla, ahora):
            return None
        cuenta = Cuenta.objects.select_for_update().get(pk=regla.cuenta_id)
        origen = f"regla '{regla.nombre}'"
        exito = True
        try:
            with transaction.atomic():
                cambios, detalle = _aplicar(regla, cuenta, origen)
        except ValidationError as e:
            cambios, detalle, exito = [], " ".join(e.messages), False
        regla.ultima_ejecucion = ahora
        regla.save(update_fields=["ultima_ejecucion"])
        return registrar(
            Evento.Tipo.EJECUCION_REGLA,
            usuario=None,
            entidad=regla,
            origen=origen,
            cambios=cambios,
            exito=exito,
            detalle=f"{cuenta.alias or cuenta}: {detalle}",
        )


def _registrar_fallo(regla_pk, ahora, error):
    """Deja el fallo como evento y marca la corrida del día (research R9). Fuera del atomic fallido."""
    with transaction.atomic():
        regla = Regla.objects.select_for_update().get(pk=regla_pk)
        regla.ultima_ejecucion = ahora
        regla.save(update_fields=["ultima_ejecucion"])
        registrar(
            Evento.Tipo.EJECUCION_REGLA,
            usuario=None,
            entidad=regla,
            origen=f"regla '{regla.nombre}'",
            exito=False,
            detalle=f"Error inesperado ({type(error).__name__}).",
        )


def correr_reglas(ahora=None):
    """Corre las reglas que tocan, en orden de hora y creación (FR-013). Devuelve cuántas."""
    ahora = ahora or timezone.now()
    corridas = 0
    for regla in Regla.objects.filter(estado=Regla.Estado.ACTIVA).order_by("hora", "pk"):
        if not toca(regla, ahora):
            continue
        try:
            if ejecutar(regla.pk, ahora):
                corridas += 1
        except Exception as error:
            logger.exception("Regla no ejecutada: regla=%s", regla.pk)
            try:
                _registrar_fallo(regla.pk, ahora, error)
            except Exception:
                logger.exception("Fallo de regla no registrado: regla=%s", regla.pk)
    return corridas


def ultimas_ejecuciones(reglas):
    """{pk de regla: (fecha en hora Argentina, "Bien" | "Sin cambios" | "Error")} con 1 consulta (R8).
    Una regla sin evento no está en el diccionario."""
    pks = [r.pk for r in reglas]
    eventos = (
        Evento.objects.filter(
            tipo=Evento.Tipo.EJECUCION_REGLA, entidad="automatizacion.regla", entidad_id__in=pks
        )
        .order_by("entidad_id", "-fecha", "-pk")
        .distinct("entidad_id")
    )
    return {
        e.entidad_id: (
            e.fecha.astimezone(ARG),
            "Error" if not e.exito else ("Bien" if e.cambios else "Sin cambios"),
        )
        for e in eventos
    }
