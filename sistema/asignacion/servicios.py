from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import ExpressionWrapper, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from sistema.avisos import servicios as avisos
from sistema.auditoria.servicios import foto, registrar_alta, registrar_cambio
from sistema.cobros.models import Cobro, PagoComplementario
from sistema.core.models import MontoField
from sistema.core.tiempo import ARG, inicio_dia_arg, rango_mes_arg, rango_semana_arg
from sistema.destinatarios.models import Cuenta

LLAVE_FILA = 4004
CERO = Decimal("0.00")


def fila():
    """Una sola fila global para elegir y reservar cupo (FR-017). Se libera al cerrar la transacción."""
    if not connection.in_atomic_block:
        raise RuntimeError("fila() necesita una transacción abierta.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [LLAVE_FILA])


def sincronizar_lista(*, usuario, origen="", sin_evento=None):
    """Deja la lista de prioridad sin huecos: saca las no habilitadas, suma las nuevas al final."""
    habilitadas = set(
        Cuenta.objects.habilitadas().filter(tipo="transferencia").values_list("pk", flat=True)
    )
    actuales = list(
        Cuenta.objects.select_for_update().filter(posicion__isnull=False).order_by("posicion")
    )
    nuevas = list(
        Cuenta.objects.select_for_update()
        .filter(pk__in=habilitadas, posicion__isnull=True)
        .order_by("pk")
    )
    orden = [c for c in actuales if c.pk in habilitadas] + nuevas
    destino = {c.pk: i + 1 for i, c in enumerate(orden)}
    sin_evento_pk = getattr(sin_evento, "pk", None)
    for cuenta in actuales + nuevas:
        posicion = destino.get(cuenta.pk)
        if cuenta.posicion == posicion:
            continue
        antes = foto(cuenta)
        cuenta.posicion = posicion
        # sin full_clean: la constraint diferida valida al cerrar la transacción
        cuenta.save(update_fields=["posicion", "fecha_cambio"])
        if cuenta.pk != sin_evento_pk:
            registrar_cambio(cuenta, antes, usuario=usuario, origen=origen)


def _suma(estado, desde, hasta):
    return Coalesce(
        Sum(
            "cobros__monto",
            filter=Q(cobros__estado=estado, cobros__fecha__gte=desde, cobros__fecha__lt=hasta),
        ),
        CERO,
        output_field=MontoField(),
    )


def _pagos(desde):
    """Pagos complementarios Registrado de la cuenta en el mes; subconsulta: sin JOIN doble (R3)."""
    return Coalesce(
        Subquery(
            PagoComplementario.objects.filter(
                cuenta=OuterRef("pk"), estado="registrado", mes=desde.date()
            )
            .values("cuenta")
            .annotate(t=Sum("monto"))
            .values("t")
        ),
        CERO,
        output_field=MontoField(),
    )


def lista_con_cupo(momento=None, *, cuentas=None):
    """Cuentas de la lista por posición, con recibido, reservado y cupo del mes y la semana.
    Con `cuentas` (un queryset), el mismo cálculo sobre esas cuentas."""
    momento = momento or timezone.now()
    mes = rango_mes_arg(momento)
    semana = rango_semana_arg(momento)
    if cuentas is None:
        cuentas = Cuenta.objects.filter(posicion__isnull=False)
    cuentas = (
        cuentas.select_related("destinatario")
        .prefetch_related("topes")
        .annotate(
            recibido_mes=ExpressionWrapper(
                _suma("finalizado", *mes) + _pagos(mes[0]),
                output_field=MontoField(),
            ),
            reservado_mes=_suma("pendiente", *mes),
            recibido_semana=_suma("finalizado", *semana),
            reservado_semana=_suma("pendiente", *semana),
        )
        .order_by("posicion")
    )
    from sistema.destinatarios.servicios import tope_de  # destinatarios importa asignacion

    lista = list(cuentas)
    for c in lista:
        # el tope que rigió en el período de `momento` (001/FR-029), no el actual
        c.tope_mensual_periodo = tope_de(c, "mensual", momento)
        c.tope_semanal_periodo = tope_de(c, "semanal", momento)
        c.excedente_mes = max(CERO, c.recibido_mes + c.reservado_mes - c.tope_mensual_periodo)
        c.cupo_mensual = max(CERO, c.tope_mensual_periodo - c.recibido_mes - c.reservado_mes)
        c.cupo_semanal = (
            None
            if c.tope_semanal_periodo == 0
            else max(CERO, c.tope_semanal_periodo - c.recibido_semana - c.reservado_semana)
        )
    return lista


def cupo_por_destinatario(destinatarios, momento=None):
    """{pk: tope, recibido, consumido, excedente, restante} sumando sus cuentas de transferencia Activas (P-20).
    Un destinatario sin esas cuentas no está en el diccionario."""
    cuentas = Cuenta.objects.filter(
        destinatario__in=list(destinatarios),
        tipo=Cuenta.Tipo.TRANSFERENCIA,
        estado=Cuenta.Estado.ACTIVA,
    )
    cupos = {}
    for c in lista_con_cupo(momento, cuentas=cuentas):
        d = cupos.setdefault(
            c.destinatario_id,
            {
                "tope": CERO,
                "recibido": CERO,
                "consumido": CERO,
                "excedente": CERO,
                "restante": CERO,
            },
        )
        d["tope"] += c.tope_mensual_periodo
        d["recibido"] += c.recibido_mes
        d["consumido"] += c.recibido_mes + c.reservado_mes
        d["excedente"] += c.excedente_mes
        d["restante"] += c.cupo_mensual
    return cupos


def semanas_del_mes(cuenta, mes):
    """Semanas de una cuenta (ver `semanas_por_cuenta`)."""
    return semanas_por_cuenta([cuenta], mes)[0][1]


def semanas_por_cuenta(cuentas, mes):
    """[(cuenta, semanas)] con 1 consulta de cobros para todas (FR-062).

    Semanas lunes a domingo (hora Argentina) que tocan el mes `mes` (un `date`): recibido,
    reservado, tope semanal y cupo de cada una. Cupo `None` si el tope es 0 (FR-027)."""
    from sistema.destinatarios.servicios import tope_de  # destinatarios importa asignacion

    desde, hasta = rango_mes_arg(inicio_dia_arg(mes))
    lunes = rango_semana_arg(desde)[0]
    inicios = []
    while lunes < hasta:
        inicios.append(lunes)
        lunes = inicio_dia_arg(lunes.date() + timedelta(days=7))
    cuentas = list(cuentas)
    recibido = {c.pk: [CERO] * len(inicios) for c in cuentas}
    reservado = {c.pk: [CERO] * len(inicios) for c in cuentas}
    cobros = Cobro.objects.filter(
        cuenta__in=cuentas,
        estado__in=[Cobro.Estado.FINALIZADO, Cobro.Estado.PENDIENTE],
        fecha__gte=inicios[0],
        fecha__lt=lunes,
    ).values_list("cuenta_id", "estado", "fecha", "monto")
    for cuenta_id, estado, fecha, monto in cobros:
        i = (fecha.astimezone(ARG).date() - inicios[0].date()).days // 7
        if estado == Cobro.Estado.FINALIZADO:
            recibido[cuenta_id][i] += monto
        else:
            reservado[cuenta_id][i] += monto
    salida = []
    for cuenta in cuentas:
        semanas = []
        for i, inicio in enumerate(inicios):
            tope = tope_de(cuenta, "semanal", inicio)
            r, v = recibido[cuenta.pk][i], reservado[cuenta.pk][i]
            semanas.append(
                {
                    "inicio": inicio.date(),
                    "fin": inicio.date() + timedelta(days=6),
                    "recibido": r,
                    "reservado": v,
                    "tope": tope,
                    "cupo": None if tope == 0 else max(CERO, tope - r - v),
                }
            )
        salida.append((cuenta, semanas))
    return salida


class SinCuentaDisponible(ValidationError):
    def __init__(self, message="No hay cuenta disponible. Cobrá con otro método de cobro."):
        super().__init__(message)


def _cumple(cuenta, monto):
    return (
        monto >= cuenta.minimo_por_pago
        and monto <= cuenta.cupo_mensual
        and (cuenta.cupo_semanal is None or monto <= cuenta.cupo_semanal)
    )


def crear_cobro_pendiente(*, monto, usuario, **datos):
    """Reserva el monto entero en la primera cuenta de la lista que lo admite (FR-014, FR-015)."""
    with transaction.atomic():
        fila()
        ahora = timezone.now()
        for cuenta in lista_con_cupo(ahora):
            if _cumple(cuenta, monto):
                cobro = Cobro(
                    cuenta=cuenta,
                    monto=monto,
                    estado=Cobro.Estado.PENDIENTE,
                    fecha=ahora,
                    creado_por=usuario,
                    destinatario=cuenta.destinatario,
                    **datos,
                )
                cobro.excedente = es_excedente(cobro)
                cobro.full_clean()
                cobro.save()
                registrar_alta(cobro, usuario=usuario)
                antes = cuenta.recibido_mes + cuenta.reservado_mes
                avisos.cerca_del_tope(
                    cuenta=cuenta, momento=ahora, antes=antes, despues=antes + monto, origen=cobro
                )
                if cobro.excedente:
                    avisos.excedente(cobro=cobro, usado=antes + monto)
                return cobro
    avisos.no_hay_cuenta(usuario=usuario, monto=monto, paciente=datos.get("nombre_paciente", ""))
    raise SinCuentaDisponible()


def usado_mes(cuenta, momento, *, excluir=None):
    """Cobros Pendiente y Finalizado del mes ARG más pagos complementarios Registrado (R6)."""
    desde, hasta = rango_mes_arg(momento)
    pagos = PagoComplementario.objects.filter(
        cuenta=cuenta, estado="registrado", mes=desde.date()
    ).aggregate(total=Coalesce(Sum("monto"), CERO, output_field=MontoField()))["total"]
    cobros = Cobro.objects.filter(
        cuenta=cuenta,
        estado__in=[Cobro.Estado.PENDIENTE, Cobro.Estado.FINALIZADO],
        fecha__gte=desde,
        fecha__lt=hasta,
    )
    if excluir is not None:
        cobros = cobros.exclude(pk=excluir)
    return (
        pagos
        + cobros.aggregate(total=Coalesce(Sum("monto"), CERO, output_field=MontoField()))["total"]
    )


def es_excedente(cobro):
    """True si recibido + reservado del mes, con este cobro, pasa el tope mensual (FR-019, FR-020)."""
    cuenta = cobro.cuenta
    if cuenta.tipo != "transferencia" or cobro.estado == Cobro.Estado.CANCELADO:
        return False
    from sistema.destinatarios.servicios import tope_de  # destinatarios importa asignacion

    tope = tope_de(cuenta, "mensual", cobro.fecha)  # el tope del mes del cobro (FR-030)
    return usado_mes(cuenta, cobro.fecha, excluir=cobro.pk) + cobro.monto > tope


def medir(pares):
    """Usado del mes de cada (cuenta, momento) de transferencia, sin repetir cuenta y mes."""
    vistos, medidos = set(), []
    for cuenta, momento in pares:
        if cuenta is None or cuenta.tipo != "transferencia":
            continue
        clave = (cuenta.pk, rango_mes_arg(momento)[0])
        if clave not in vistos:
            vistos.add(clave)
            medidos.append((cuenta, momento, usado_mes(cuenta, momento)))
    return medidos


def avisar_cruces(medidos, origen):
    for cuenta, momento, antes in medidos:
        avisos.cerca_del_tope(
            cuenta=cuenta,
            momento=momento,
            antes=antes,
            despues=usado_mes(cuenta, momento),
            origen=origen,
        )


def mover_cuenta(cuenta, posicion, *, usuario, origen=""):
    """Mueve la cuenta a `posicion` y corre las de en medio un lugar (FR-003)."""
    with transaction.atomic():
        fila()
        lista = list(
            Cuenta.objects.select_for_update().filter(posicion__isnull=False).order_by("posicion")
        )
        actual = next((c for c in lista if c.pk == cuenta.pk), None)
        if actual is None:
            raise ValidationError("La cuenta no está en la lista de prioridad.")
        if not 1 <= posicion <= len(lista):
            raise ValidationError(f"La posición debe ir de 1 a {len(lista)}.")
        if actual.posicion == posicion:
            return actual
        lista.remove(actual)
        lista.insert(posicion - 1, actual)
        for i, c in enumerate(lista, start=1):
            if c.posicion == i:
                continue
            antes = foto(c)
            c.posicion = i
            # sin full_clean: la constraint diferida valida al cerrar la transacción
            c.save(update_fields=["posicion", "fecha_cambio"])
            registrar_cambio(c, antes, usuario=usuario, origen=origen)
        return actual
