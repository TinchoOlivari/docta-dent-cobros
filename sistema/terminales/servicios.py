import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.timezone import localtime

from sistema.auditoria.servicios import (
    foto,
    registrar_alta,
    registrar_borrado,
    registrar_cambio,
)
from sistema.arca import servicios as arca
from sistema.avisos import servicios as avisos
from sistema.cobros.models import Cobro
from sistema.core.models import MontoField
from sistema.dentalink.servicios import al_cambiar_cobro
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import _guardar
from sistema.terminales.models import Sesion, Terminal

logger = logging.getLogger(__name__)

CERO = Decimal("0.00")


def acumulado(cuenta) -> Decimal:
    """FR-025: cobros de la cuenta, no cancelados, desde «acumula desde»."""
    return (
        Cobro.objects.filter(cuenta=cuenta, fecha__gte=cuenta.acumula_desde)
        .exclude(estado=Cobro.Estado.CANCELADO)
        .aggregate(t=Coalesce(Sum("monto"), CERO, output_field=MontoField()))["t"]
    )


def cruza_objetivo(antes, despues, objetivo) -> bool:
    """FR-028."""
    return antes < objetivo <= despues


def avisar_objetivo(cuenta, antes) -> bool:
    """007 lo llama dentro de su atomic(), después del alta, cambio o cancelación (007/FR-028,
    007/FR-040, 007/FR-042). 007 toma `antes = acumulado(cuenta)` antes del alta, cambio o
    cancelación y llama `avisar_objetivo(cuenta, antes)` después (US5.1 a US5.3)."""
    despues = acumulado(cuenta)
    avisos.cerca_del_objetivo(cuenta=cuenta, antes=antes, despues=despues)
    if not cruza_objetivo(antes, despues, cuenta.objetivo):
        return False
    logger.info("Cuenta %s alcanzó su objetivo", cuenta.pk)
    avisos.objetivo_alcanzado(cuenta=cuenta, acumulado=despues)
    return True


def cuenta_en(terminal, fecha):
    """FR-007, FR-014: cuenta de la sesión que cubre `fecha`, o None."""
    sesion = (
        Sesion.objects.filter(terminal=terminal, inicio__lte=fecha)
        .filter(Q(fin__isnull=True) | Q(fin__gt=fecha))
        .select_related("cuenta")
        .first()
    )
    return sesion.cuenta if sesion else None


def cuenta_para_cobro(terminal, fecha):
    """007 lo llama dentro de su atomic() (007/FR-028, 007/FR-040, 007/FR-042).

    Bloquea la terminal: espera a una corrección de sesión en curso (FR-021). No mira el estado
    de la terminal: 007 exige Activa en el alta (FR-003).
    """
    terminal = Terminal.objects.select_for_update().get(pk=terminal.pk)
    cuenta = cuenta_en(terminal, fecha)
    if cuenta is None:
        raise ValidationError("La terminal no tiene una sesión en esa fecha y hora.")
    return cuenta


def terminales_disponibles(metodo):
    """FR-031: terminales Activas con sesión abierta que aceptan `metodo`."""
    if metodo not in ("credito", "debito", "qr", "link"):
        return Terminal.objects.none()
    # `sesiones__fin__isnull=True` solo daría también las terminales sin ninguna sesión (LEFT JOIN).
    abierta = Sesion.objects.filter(terminal=OuterRef("pk"), fin__isnull=True)
    return Terminal.objects.filter(
        Exists(abierta), estado=Terminal.Estado.ACTIVA, **{f"acepta_{metodo}": True}
    ).order_by("nombre")


def _reasignar(terminal, desde, hasta) -> int:
    """FR-018 a FR-022: lleva cada cobro de la terminal en el rango a la cuenta de su sesión."""
    cobros = Cobro.objects.select_for_update().filter(terminal=terminal, fecha__gte=desde)
    if hasta is not None:
        cobros = cobros.filter(fecha__lt=hasta)
    antes = {}
    cuentas = {}
    cantidad = 0
    for cobro in cobros.order_by("fecha", "pk"):
        cuenta = cuenta_en(terminal, cobro.fecha)
        if cuenta is None:
            raise ValidationError(
                f"El cobro {cobro.pk} del {localtime(cobro.fecha):%d/%m/%Y %H:%M:%S} "
                "queda sin sesión."
            )
        if cuenta.pk == cobro.cuenta_id:
            continue
        if cuenta.pk not in antes:
            antes[cuenta.pk] = acumulado(cuenta)
            cuentas[cuenta.pk] = cuenta
        antes_cobro = foto(cobro)
        cobro.cuenta = cuenta
        cobro.destinatario = cuenta.destinatario
        cobro.save()
        registrar_cambio(cobro, antes_cobro, usuario=None, origen="corrección de sesión")
        al_cambiar_cobro(cobro, usuario=None, cancelado=False)
        arca.al_cambiar_cobro(cobro, antes_cobro, usuario=None, origen="corrección de sesión")
        if cobro.destinatario_id != antes_cobro["destinatario_id"]:
            arca.al_finalizar(
                cobro
            )  # 014/SC-001: como `editar_cobro`; no repite una factura vigente
        cantidad += 1
    for pk, cuenta in cuentas.items():
        avisar_objetivo(cuenta, antes[pk])
    return cantidad


def crear_terminal(*, usuario, nombre, proveedor, **datos):
    """FR-001, FR-002: alta Activa con `fecha_activacion`."""
    with transaction.atomic():
        terminal = Terminal(nombre=nombre, proveedor=proveedor, **datos)
        terminal.fecha_activacion = timezone.now()
        _guardar(terminal)
        registrar_alta(terminal, usuario=usuario)
        return terminal


def editar_terminal(terminal, *, usuario, **datos):
    """FR-001, FR-002: nombre, métodos y observaciones."""
    for campo in ("proveedor", "estado"):
        if campo in datos:
            raise ValidationError(f"El campo {campo} no se cambia al editar la terminal.")
    with transaction.atomic():
        terminal = Terminal.objects.select_for_update().get(pk=terminal.pk)
        antes = foto(terminal)
        for campo, valor in datos.items():
            setattr(terminal, campo, valor)
        if foto(terminal) == antes:
            return terminal
        _guardar(terminal)
        registrar_cambio(terminal, antes, usuario=usuario)
        return terminal


def cambiar_estado_terminal(terminal, estado, *, usuario):
    """FR-003, FR-004. Bloquea la terminal: serializa con `abrir_sesion`."""
    with transaction.atomic():
        terminal = Terminal.objects.select_for_update().get(pk=terminal.pk)
        if terminal.estado == estado:
            return terminal
        if estado != Terminal.Estado.ACTIVA and terminal.sesiones.filter(fin__isnull=True).exists():
            raise ValidationError("La terminal tiene una sesión abierta. Cerrá la sesión primero.")
        antes = foto(terminal)
        terminal.estado = estado
        if estado == Terminal.Estado.ACTIVA:
            terminal.fecha_activacion = timezone.now()
        elif estado == Terminal.Estado.ARCHIVADA:
            terminal.fecha_archivado = timezone.now()
        _guardar(terminal)
        registrar_cambio(terminal, antes, usuario=usuario)
        return terminal


def _bloquear(terminal):
    return Terminal.objects.select_for_update().get(pk=terminal.pk)


def _exigir_habilitada(terminal, cuenta):
    """FR-009. Orden de bloqueo: terminal (ya bloqueada) -> destinatario -> cuenta (R5)."""
    destinatario = Destinatario.objects.select_for_update().get(pk=cuenta.destinatario_id)
    cuenta = Cuenta.objects.select_for_update().get(pk=cuenta.pk)
    if cuenta.destinatario_id != destinatario.pk:
        # La cuenta cambió de destinatario mientras esperaba el bloqueo: valida el de la fila.
        destinatario = Destinatario.objects.select_for_update().get(pk=cuenta.destinatario_id)
    if terminal.estado != Terminal.Estado.ACTIVA:
        raise ValidationError("La terminal no está Activa.")
    if cuenta.estado != Cuenta.Estado.ACTIVA:
        raise ValidationError("La cuenta no está Activa.")
    if destinatario.estado != Destinatario.Estado.ACTIVO:
        raise ValidationError("El destinatario de la cuenta no está Activo.")
    return cuenta


def _no_futuro(valor, ahora, nombre):
    """FR-010."""
    if valor > ahora:
        raise ValidationError(f"El {nombre} no puede estar en el futuro.")


def abrir_sesion(terminal, cuenta, inicio, *, usuario):
    """FR-006 a FR-011."""
    with transaction.atomic():
        ahora = timezone.now()
        terminal = _bloquear(terminal)
        cuenta = _exigir_habilitada(terminal, cuenta)
        _no_futuro(inicio, ahora, "inicio")
        sesion = Sesion(terminal=terminal, cuenta=cuenta, inicio=inicio)
        _guardar(sesion)
        registrar_alta(sesion, usuario=usuario)
        return sesion


def cambiar_cuenta(terminal, cuenta, hora, *, usuario):
    """FR-013: cierra la sesión abierta en `hora` y abre la de `cuenta`."""
    with transaction.atomic():
        ahora = timezone.now()
        terminal = _bloquear(terminal)
        actual = terminal.sesiones.select_for_update().filter(fin__isnull=True).first()
        if actual is None:
            raise ValidationError("La terminal no tiene una sesión abierta.")
        _no_futuro(hora, ahora, "cambio")
        if hora <= actual.inicio:
            raise ValidationError("El cambio debe ser posterior al inicio de la sesión actual.")
        cuenta = _exigir_habilitada(terminal, cuenta)
        antes = foto(actual)
        actual.fin = hora
        _guardar(actual)
        registrar_cambio(actual, antes, usuario=usuario)
        nueva = Sesion(terminal=terminal, cuenta=cuenta, inicio=hora)
        _guardar(nueva)
        registrar_alta(nueva, usuario=usuario)
        if hora < ahora:
            _reasignar(terminal, hora, None)
        return nueva


def cerrar_sesion(sesion, fin, *, usuario):
    """FR-006, FR-010, FR-020."""
    with transaction.atomic():
        ahora = timezone.now()
        terminal = _bloquear(sesion.terminal)
        sesion = Sesion.objects.select_for_update().get(pk=sesion.pk)
        if sesion.fin is not None:
            raise ValidationError("La sesión ya está cerrada.")
        _no_futuro(fin, ahora, "fin")
        antes = foto(sesion)
        sesion.fin = fin
        _guardar(sesion)
        registrar_cambio(sesion, antes, usuario=usuario)
        _reasignar(terminal, fin, None)
        return sesion


def _rango_union(viejo, nuevo):
    """Unión de dos rangos [inicio, fin) de una sesión; `fin=None` es infinito."""
    desde = min(viejo[0], nuevo[0])
    hasta = None if None in (viejo[1], nuevo[1]) else max(viejo[1], nuevo[1])
    return desde, hasta


def editar_sesion(sesion, *, usuario, **datos):
    """FR-016, FR-018 a FR-022: corrige `inicio`, `fin` o `cuenta` de una sesión."""
    for campo in datos:
        if campo not in ("inicio", "fin", "cuenta"):
            raise ValidationError(f"El campo {campo} no se cambia al editar la sesión.")
    with transaction.atomic():
        ahora = timezone.now()
        terminal = _bloquear(sesion.terminal)
        sesion = Sesion.objects.select_for_update().get(pk=sesion.pk)
        antes = foto(sesion)
        viejo = (sesion.inicio, sesion.fin)
        for campo in ("inicio", "fin"):
            if datos.get(campo) is not None:
                _no_futuro(datos[campo], ahora, campo)
        cuenta = datos.get("cuenta")
        if cuenta is not None and cuenta.pk != sesion.cuenta_id:
            sesion.cuenta = cuenta
        for campo in ("inicio", "fin"):
            if campo in datos:
                setattr(sesion, campo, datos[campo])
        # FR-009 sobre el resultado: queda abierta y (estaba cerrada o cambió la cuenta).
        # Una sesión que sigue cerrada: solo `clean()` (tipo y proveedor), cualquier estado (FR-016).
        if sesion.fin is None and (
            antes["fin"] is not None or sesion.cuenta_id != antes["cuenta_id"]
        ):
            sesion.cuenta = _exigir_habilitada(terminal, sesion.cuenta)
        if foto(sesion) == antes:
            return sesion
        _guardar(sesion)
        registrar_cambio(sesion, antes, usuario=usuario)
        _reasignar(terminal, *_rango_union(viejo, (sesion.inicio, sesion.fin)))
        return sesion


def corregir_cambio(sesion, hora, *, usuario):
    """FR-017: mueve la hora en que la sesión `sesion` (A) pasa a la sesión seguida (B).

    Devuelve la cantidad de cobros que cambiaron de cuenta.
    """
    with transaction.atomic():
        ahora = timezone.now()
        terminal = _bloquear(sesion.terminal)
        a = Sesion.objects.select_for_update().get(pk=sesion.pk)
        b = None
        if a.fin is not None:
            b = terminal.sesiones.select_for_update().filter(inicio=a.fin).first()
        if b is None:
            raise ValidationError("La sesión no tiene una sesión seguida.")
        _no_futuro(hora, ahora, "cambio")
        vieja = a.fin
        if hora == vieja:
            return 0
        # Primero la sesión que se achica: nunca hay superposición intermedia (research R2).
        for obj, campo in (
            ((a, "fin"), (b, "inicio")) if hora < vieja else ((b, "inicio"), (a, "fin"))
        ):
            antes = foto(obj)
            setattr(obj, campo, hora)
            _guardar(obj)
            registrar_cambio(obj, antes, usuario=usuario)
        return _reasignar(terminal, min(vieja, hora), max(vieja, hora))


def borrar_sesion(sesion, *, usuario):
    """FR-023: solo una sesión sin cobros de su terminal en su rango, de cualquier estado."""
    with transaction.atomic():
        terminal = _bloquear(sesion.terminal)
        sesion = Sesion.objects.select_for_update().get(pk=sesion.pk)
        cobros = Cobro.objects.filter(terminal=terminal, fecha__gte=sesion.inicio)
        if sesion.fin is not None:
            cobros = cobros.filter(fecha__lt=sesion.fin)
        if cobros.exists():
            raise ValidationError("La sesión tiene cobros. No se puede borrar.")
        registrar_borrado(sesion, usuario=usuario)
        sesion.delete()


def cambiar_acumula_desde(cuenta, fecha, *, usuario):
    """FR-026, FR-029: reinicia «acumula desde». No avisa el objetivo."""
    with transaction.atomic():
        ahora = timezone.now()
        cuenta = Cuenta.objects.select_for_update().get(pk=cuenta.pk)
        if cuenta.tipo != Cuenta.Tipo.TERMINAL_POS:
            raise ValidationError("Solo una cuenta de terminal POS tiene «acumula desde».")
        if fecha > ahora:
            raise ValidationError("«Acumula desde» no puede estar en el futuro.")
        antes = foto(cuenta)
        cuenta.acumula_desde = fecha
        _guardar(cuenta)
        registrar_cambio(cuenta, antes, usuario=usuario)
        return cuenta


def cuentas_logueadas() -> list[Sesion]:
    """Sesiones abiertas con `.cobrado` y `.objetivo`, de la más cerca del objetivo a la más
    lejos (019/FR-007, FR-009). El objetivo de una cuenta POS es siempre mayor que cero."""
    sesiones = list(
        Sesion.objects.filter(fin__isnull=True).select_related("terminal", "cuenta__destinatario")
    )
    for s in sesiones:
        # ponytail: 1 consulta por sesión; con decenas de terminales, Subquery del acumulado.
        s.cobrado = acumulado(s.cuenta)
        s.objetivo = s.cuenta.objetivo
    sesiones.sort(key=lambda s: (-s.cobrado / s.objetivo, -s.cobrado, s.terminal.nombre))
    return sesiones
