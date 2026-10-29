"""Reglas de 005: lectura de Dentalink, copia, configuración y envío."""

import logging
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import foto, registrar, registrar_alta, registrar_cambio
from sistema.avisos import servicios as avisos
from sistema.cobros.models import Cobro, ItemCobro
from sistema.core.tiempo import ARG

from . import cliente
from .cliente import DentalinkNoDisponible
from .models import Banco, ConfiguracionDentalink, MedioPago, Profesional

logger = logging.getLogger(__name__)

CERO = Decimal("0.00")
MAX_INTENTOS = 6
MINUTOS_COLGADO = 10
ERROR_COLGADO = "El envío no terminó. Revisá si el pago está en Dentalink."
ESPERAS = [1, 2, 4, 8, 16]  # minutos después de cada falla reintentable (research R4)
# TODO(spec-pendiente: T060, confirmar con la API real los nombres de medios, sobre todo "Contado")
MEDIO_POR_METODO = {
    "efectivo": "Efectivo",
    "transferencia": "Transferencia",
    "debito": "Tarjeta de débito",
    "credito": "Tarjeta de crédito",
    "qr": "Contado",
    "link": "Contado",
}
CAMPOS_ENVIO = ("estado_envio", "id_pago_dentalink", "intentos")


def buscar_pacientes(texto):
    texto = (texto or "").strip()
    if not texto:
        return []
    return cliente.buscar_pacientes(texto)


def datos_paciente(id_paciente):
    """Nombre, DNI (021/FR-004) y nacimiento (023/FR-033) para mostrar, en un solo pedido.

    Sin dato (Dentalink caído): None en cada uno; la vista muestra el N.º.
    """
    try:
        p = cliente.paciente(id_paciente)
    except DentalinkNoDisponible:
        p = {}
    nombre = p.get("nombre")
    return {
        "nombre_paciente": nombre if isinstance(nombre, str) and nombre else None,
        "dni_paciente": p.get("dni") or None,
        "nacimiento_paciente": p.get("nacimiento"),
    }


def nacimiento_paciente(id_paciente):
    """Fecha de nacimiento del paciente para mostrarla (023/FR-033). Sin dato: None."""
    try:
        return cliente.paciente(id_paciente)["nacimiento"]
    except (DentalinkNoDisponible, KeyError, TypeError):
        return None


def prestaciones_de(id_paciente):
    """Tratamientos con las prestaciones a cobrar (FR-002 a FR-004, research R6)."""
    id_saldo_a_favor = ConfiguracionDentalink.actual().id_prestacion_saldo_a_favor
    resultado = []
    for t in cliente.tratamientos(id_paciente):
        nombres = {p["id"]: p["nombre"] for p in t["profesionales"]}
        profesionales = []
        for id_prof in [t["id_profesional"], *nombres]:
            if id_prof is not None and id_prof in nombres:
                if id_prof not in [p["id"] for p in profesionales]:
                    profesionales.append({"id": id_prof, "nombre": nombres[id_prof]})
        prestaciones = []
        for p in t["prestaciones"]:
            saldo = p["total"] - p["pagado"]
            a_favor = id_saldo_a_favor is not None and p["id_prestacion"] == id_saldo_a_favor
            if saldo <= CERO and not a_favor:
                continue
            id_prof = p["id_profesional"] or t["id_profesional"]
            propuesto = {"id": id_prof, "nombre": nombres.get(id_prof, "")}
            lista = list(profesionales)
            if id_prof is not None and id_prof not in [x["id"] for x in lista]:
                lista.append(propuesto)
            prestaciones.append(
                {
                    **p,
                    "saldo": max(saldo, CERO),
                    "saldo_a_favor": a_favor,
                    "profesional_propuesto": propuesto,
                    "profesionales": lista,
                }
            )
        if prestaciones:
            resultado.append({"id": t["id"], "nombre": t["nombre"], "prestaciones": prestaciones})
    return resultado


def actualizar_copia(*, usuario):
    """FR-005: lee las 3 listas y recién después escribe. Si una falla, no cambia nada."""
    listas = [
        (Banco, cliente.listar("bancos")),
        (Profesional, cliente.listar("dentistas")),
        (MedioPago, cliente.listar("mediospago")),
    ]
    total = {"altas": 0, "cambios": 0, "bajas": 0}
    with transaction.atomic():
        for modelo, items in listas:
            vistos = set()
            for item in items:
                vistos.add(item["id"])
                obj = modelo.objects.filter(id_dentalink=item["id"]).first()
                if obj is None:
                    obj = modelo.objects.create(id_dentalink=item["id"], nombre=item["nombre"])
                    registrar_alta(obj, usuario=usuario)
                    total["altas"] += 1
                elif obj.nombre != item["nombre"] or not obj.vigente:
                    antes = foto(obj)
                    obj.nombre, obj.vigente = item["nombre"], True
                    obj.save()
                    registrar_cambio(obj, antes, usuario=usuario)
                    total["cambios"] += 1
            for obj in modelo.objects.filter(vigente=True).exclude(id_dentalink__in=vistos):
                antes = foto(obj)
                obj.vigente = False
                obj.save()
                registrar_cambio(obj, antes, usuario=usuario)
                total["bajas"] += 1
    return total


def guardar_configuracion(
    *, usuario, id_prestacion_saldo_a_favor, nombre_prestacion_saldo_a_favor, banco_por_defecto
):
    if banco_por_defecto is not None and not banco_por_defecto.vigente:
        raise ValidationError("Elegí un banco vigente de la copia de Dentalink.")
    with transaction.atomic():
        config = ConfiguracionDentalink.actual()
        config = ConfiguracionDentalink.objects.select_for_update().get(pk=config.pk)
        antes = foto(config)
        config.id_prestacion_saldo_a_favor = id_prestacion_saldo_a_favor
        config.nombre_prestacion_saldo_a_favor = nombre_prestacion_saldo_a_favor
        config.banco_por_defecto = banco_por_defecto
        config.full_clean()
        config.save()
        registrar_cambio(config, antes, usuario=usuario)
    return config


class FaltaDato(Exception):
    """El ítem no se puede armar con la copia de Dentalink: no se llama a Dentalink."""


def cobro_listo(cobro):
    """FR-009: Finalizado, sin marca 'faltan ítems' y ítems que suman el monto exacto."""
    if cobro.estado != Cobro.Estado.FINALIZADO or cobro.faltan_items:
        return False
    datos = cobro.items.aggregate(suma=Coalesce(Sum("monto"), CERO))
    return cobro.items.exists() and datos["suma"] == cobro.monto


def encolar_envio(cobro):
    from . import tasks  # tasks.py importa servicios.py

    if cobro_listo(cobro):
        transaction.on_commit(lambda: tasks.enviar_cobro.delay(cobro.pk))


def armar_pago(item):
    """FR-010 y FR-011: el cuerpo del pago a crear en Dentalink."""
    cobro = item.cobro
    nombre = MEDIO_POR_METODO[cobro.metodo]
    medio = MedioPago.objects.filter(nombre__iexact=nombre, vigente=True).first()
    if medio is None:
        raise FaltaDato(f"El medio de pago '{nombre}' no está en la copia de Dentalink.")
    monto = f"{item.monto:.2f}"
    datos = {
        "id_paciente": cobro.id_paciente_dentalink,
        "id_medio_pago": medio.id_dentalink,
        "monto_pago": monto,
        "fecha_recepcion": cobro.fecha.astimezone(ARG).date().isoformat(),
        "abonos": [{"id_detalle": item.id_prestacion, "monto_abono": monto}],
    }
    # TODO(spec-pendiente: T061, caja de Dentalink por API; espera respuesta de soporte)
    if cobro.metodo == Cobro.Metodo.TRANSFERENCIA:
        datos["numero_referencia"] = item.referencia
        banco = None
        if cobro.cuenta.banco:
            banco = Banco.objects.filter(nombre__iexact=cobro.cuenta.banco, vigente=True).first()
        banco = banco or ConfiguracionDentalink.actual().banco_por_defecto
        if banco is None:
            raise FaltaDato("La cuenta no tiene banco y no hay banco por defecto.")
        datos["id_banco"] = banco.id_dentalink
    elif cobro.metodo in (Cobro.Metodo.DEBITO, Cobro.Metodo.CREDITO) and cobro.banco_emisor:
        datos["id_banco"] = cobro.banco_emisor.id_dentalink
    return datos


def avisar_falla(item):
    logger.warning(
        "Envío a Dentalink en %s: item=%s", item.get_estado_envio_display(), item.referencia
    )
    avisos.item_con_falla(item)


def _cambios(antes, item):
    despues = foto(item)
    return [
        {"campo": c, "anterior": antes[c], "nuevo": despues[c]}
        for c in CAMPOS_ENVIO
        if antes[c] != despues[c]
    ]


def _evento(item, antes, tipo, error):
    registrar(
        Evento.Tipo.ENVIO_DENTALINK,
        usuario=None,
        entidad=item,
        cambios=_cambios(antes, item),
        exito=(tipo == "ok"),
        origen="envío automático",
        detalle=f"Intento {item.intentos}: {tipo}. {error}",
    )


def _guardar(item):
    item.full_clean()
    item.save()


def enviar_item(item_id, *, ahora=None):
    """Envía un ítem a Dentalink en 3 etapas (research R4). Devuelve el estado final u 'omitido'."""
    ahora = ahora or timezone.now()
    Estado = ItemCobro.EstadoEnvio
    with transaction.atomic():
        item = (
            ItemCobro.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("cobro__cuenta")
            .filter(pk=item_id)
            .first()
        )
        if (
            item is None
            or item.estado_envio != Estado.PENDIENTE
            or item.en_curso_desde is not None
            or (item.proximo_intento is not None and item.proximo_intento > ahora)
            or not cobro_listo(item.cobro)
        ):
            return "omitido"
        antes = foto(item)
        try:
            datos = armar_pago(item)
        except FaltaDato as exc:
            item.estado_envio = Estado.ERROR
            item.ultimo_error = str(exc)
            _guardar(item)
            _evento(item, antes, "falta_dato", str(exc))
            avisar_falla(item)
            return item.estado_envio
        item.intentos += 1
        item.en_curso_desde = ahora
        _guardar(item)

    resultado = cliente.crear_pago(datos)  # fuera de toda transacción

    with transaction.atomic():
        item = ItemCobro.objects.select_for_update().get(pk=item_id)
        item.en_curso_desde = None
        if item.estado_envio != Estado.PENDIENTE:
            # Otro proceso cambió el ítem durante la llamada (FR-023, FR-024): no se pisa el estado.
            if resultado.tipo == "ok":
                item.id_pago_dentalink = resultado.id_pago
            _guardar(item)
            _evento(item, antes, resultado.tipo, resultado.error)
            return item.estado_envio
        item.proximo_intento = None
        item.ultimo_error = resultado.error
        if resultado.tipo == "ok":
            item.estado_envio = Estado.ENVIADO
            item.id_pago_dentalink = resultado.id_pago
        elif resultado.tipo == "reintentable" and item.intentos < MAX_INTENTOS:
            item.proximo_intento = ahora + timedelta(minutes=ESPERAS[item.intentos - 1])
        elif resultado.tipo in ("reintentable", "rechazado"):
            item.estado_envio = Estado.ERROR
        else:  # dudoso, sin_id
            item.estado_envio = Estado.REVISAR
        _guardar(item)
        _evento(item, antes, resultado.tipo, resultado.error)
    if item.estado_envio in (Estado.ERROR, Estado.REVISAR):
        avisar_falla(item)
    return item.estado_envio


def liberar_colgados(*, ahora=None):
    """FR-017: un envío en curso hace más de 10 minutos pasa a 'revisar' (no se reenvía solo)."""
    ahora = ahora or timezone.now()
    limite = ahora - timedelta(minutes=MINUTOS_COLGADO)
    Estado = ItemCobro.EstadoEnvio
    liberados = 0
    with transaction.atomic():
        items = ItemCobro.objects.select_for_update(skip_locked=True, of=("self",)).filter(
            estado_envio=Estado.PENDIENTE, en_curso_desde__lt=limite
        )
        for item in items:
            antes = foto(item)
            item.estado_envio = Estado.REVISAR
            item.en_curso_desde = None
            item.ultimo_error = ERROR_COLGADO
            _guardar(item)
            _evento(item, antes, "colgado", ERROR_COLGADO)
            avisar_falla(item)
            liberados += 1
    return liberados


def _cambiar_estado(item, nuevo, *, usuario, permitidos, accion, detalle="", **campos):
    """FR-021 a FR-023: cambia el estado de envío bajo bloqueo y deja el evento."""
    with transaction.atomic():
        item = ItemCobro.objects.select_for_update().select_related("cobro").get(pk=item.pk)
        if item.estado_envio not in permitidos:
            raise ValidationError(
                f"El ítem no se puede {accion} en estado {item.get_estado_envio_display()}."
            )
        antes = foto(item)
        item.estado_envio = nuevo
        for campo, valor in campos.items():
            setattr(item, campo, valor)
        item.save()
        registrar_cambio(item, antes, usuario=usuario, detalle=detalle)
    return item


def reintentar(item, *, usuario):
    Estado = ItemCobro.EstadoEnvio
    item = _cambiar_estado(
        item,
        Estado.PENDIENTE,
        usuario=usuario,
        permitidos=(Estado.ERROR, Estado.REVISAR),
        accion="reintentar",
        intentos=0,
        proximo_intento=None,
        ultimo_error="",
    )
    encolar_envio(item.cobro)
    return item


def marcar_enviado(item, *, usuario):
    Estado = ItemCobro.EstadoEnvio
    detalle = (
        "Verificado en Dentalink"
        if item.estado_envio == Estado.REVISAR
        else "Cargado a mano en Dentalink"
    )
    return _cambiar_estado(
        item,
        Estado.ENVIADO,
        usuario=usuario,
        permitidos=(Estado.ERROR, Estado.REVISAR),
        accion="marcar como enviado",
        detalle=detalle,
    )


def marcar_ajustado(item, *, usuario):
    Estado = ItemCobro.EstadoEnvio
    return _cambiar_estado(
        item,
        Estado.AJUSTADO,
        usuario=usuario,
        permitidos=(Estado.REQUIERE_AJUSTE,),
        accion="marcar como ajustado",
    )


def al_cambiar_cobro(cobro, *, usuario, cancelado):
    """FR-023 y FR-024, para 007: va dentro del atomic() del llamador."""
    Estado = ItemCobro.EstadoEnvio
    nuevo = {Estado.ENVIADO: Estado.REQUIERE_AJUSTE}
    if cancelado:
        nuevo |= {
            Estado.REVISAR: Estado.REQUIERE_AJUSTE,
            Estado.PENDIENTE: Estado.CANCELADO,
            Estado.ERROR: Estado.CANCELADO,
        }
    for item in ItemCobro.objects.select_for_update().filter(cobro=cobro).order_by("numero"):
        # Pendiente en curso: el pago pudo crearse con los datos viejos.
        en_curso = item.estado_envio == Estado.PENDIENTE and item.en_curso_desde is not None
        if en_curso or item.estado_envio in nuevo:
            antes = foto(item)
            item.estado_envio = Estado.REQUIERE_AJUSTE if en_curso else nuevo[item.estado_envio]
            item.save()
            registrar_cambio(item, antes, usuario=usuario)
