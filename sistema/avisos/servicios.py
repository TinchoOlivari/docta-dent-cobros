import functools
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import localtime

from sistema.auditoria.servicios import foto, registrar_alta, registrar_borrado, registrar_cambio
from sistema.cobros.models import Cobro, ItemCobro
from sistema.core.templatetags.pesos import pesos
from sistema.core.tiempo import ARG
from sistema.terminales.models import Sesion, Terminal

from . import tasks
from .models import ConfiguracionAvisos, DireccionAviso

logger = logging.getLogger(__name__)

MARGEN_POR_DEFECTO = 10
CERO = Decimal("0.00")


def margen():
    # No escribe: corre en el camino del cobro.
    valor = ConfiguracionAvisos.objects.filter(pk=1).values_list("margen", flat=True).first()
    return MARGEN_POR_DEFECTO if valor is None else valor


def umbral(base, margen):
    return (base * (100 - margen) / Decimal(100)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _fecha(dt):
    return localtime(dt, ARG).strftime("%d/%m/%Y %H:%M:%S")


def _url(nombre, *args, ancla=""):
    return settings.SISTEMA_URL + reverse(nombre, args=args) + ancla


def _cuenta(cuenta):
    # Nunca el CBU (FR-013).
    return str(cuenta)


def _enviar(asunto, lineas, *, ya=False):
    cuerpo = "\n".join(lineas)

    def encolar():
        for direccion in DireccionAviso.objects.values_list("email", flat=True):
            tasks.enviar_aviso.delay(direccion, asunto, cuerpo)

    if ya:
        try:
            encolar()
        except Exception:
            logger.exception("Aviso no encolado: asunto=%s", asunto)
    else:
        transaction.on_commit(encolar, robust=True)


def _seguro(f):
    """Un error al armar el aviso nunca deshace ni frena el cambio (FR-015)."""

    @functools.wraps(f)
    def envuelta(*args, **kwargs):
        try:
            with transaction.atomic():
                return f(*args, **kwargs)
        except Exception:
            logger.exception("Aviso no armado: %s", f.__name__)

    return envuelta


def guardar_margen(*, usuario, margen):
    # Nunca avisa (FR-004): cambiar el margen no dispara emails.
    with transaction.atomic():
        ConfiguracionAvisos.actual()
        conf = ConfiguracionAvisos.objects.select_for_update().get(pk=1)
        antes = foto(conf)
        conf.margen = margen
        conf.full_clean()
        conf.save()
        registrar_cambio(conf, antes, usuario=usuario)
    return conf


def agregar_direccion(*, usuario, email):
    try:
        with transaction.atomic():
            d = DireccionAviso(email=email)
            d.full_clean()
            d.save()
            registrar_alta(d, usuario=usuario)
    except IntegrityError:
        raise ValidationError("La dirección ya está en la lista.") from None
    return d


def borrar_direccion(direccion, *, usuario):
    with transaction.atomic():
        d = DireccionAviso.objects.select_for_update().get(pk=direccion.pk)
        registrar_borrado(d, usuario=usuario)
        d.delete()


@_seguro
def cerca_del_tope(*, cuenta, momento, antes, despues, origen):
    from sistema.destinatarios.servicios import tope_de  # destinatarios importa avisos

    tope = tope_de(cuenta, "mensual", momento)
    m = margen()
    if not antes < umbral(tope, m) <= despues:
        return
    lineas = [
        f"Cuenta: {_cuenta(cuenta)}",
        f"Destinatario: {cuenta.destinatario.nombre}",
        f"Mes: {localtime(momento, ARG).strftime('%m/%Y')}",
        f"Tope mensual: {pesos(tope)}",
        f"Margen: {m} %",
        f"Recibido y reservado del mes: {pesos(despues)}",
        f"Cupo: {pesos(max(CERO, tope - despues))}",
    ]
    if isinstance(origen, Cobro):
        lineas += [f"Cobro {origen.pk}", f"Paciente: {origen.nombre_paciente}"]
    else:
        lineas.append(f"Pago complementario {origen.pk}")
    lineas.append(f"Lista de prioridad: {_url('asignacion:lista')}")
    _enviar(f"Cuenta {_cuenta(cuenta)} cerca del tope", lineas)


@_seguro
def excedente(*, cobro, usado):
    from sistema.destinatarios.servicios import tope_de  # destinatarios importa avisos

    cuenta = cobro.cuenta
    tope = tope_de(cuenta, "mensual", cobro.fecha)
    _enviar(
        f"Cobro excedente: cuenta {_cuenta(cuenta)}",
        [
            f"Cobro: {cobro.pk}",
            f"Fecha: {_fecha(cobro.fecha)}",
            f"Paciente: {cobro.nombre_paciente}",
            f"Monto: {pesos(cobro.monto)}",
            f"Método de cobro: {cobro.get_metodo_display()}",
            f"Cuenta: {_cuenta(cuenta)}",
            f"Destinatario: {cuenta.destinatario.nombre}",
            f"Tope mensual: {pesos(tope)}",
            f"Recibido y reservado del mes: {pesos(usado)}",
            f"Excedente: {pesos(usado - tope)}",
            f"Comentario del operador: {cobro.observaciones or '—'}",
            f"Ver el cobro: {_url('cobros:detalle', cobro.pk)}",
        ],
    )


@_seguro
def no_hay_cuenta(*, usuario, monto, paciente):
    _enviar(
        f"No hay cuenta disponible para {pesos(monto)}",
        [
            f"Fecha y hora: {_fecha(timezone.now())}",
            f"Operador: {getattr(usuario, 'username', 'Sistema')}",
            f"Paciente: {paciente}",
            f"Monto pedido: {pesos(monto)}",
            f"Lista de prioridad: {_url('asignacion:lista')}",
        ],
        ya=True,
    )


def _terminales(cuenta):
    nombres = (
        Terminal.objects.filter(sesiones__in=Sesion.objects.filter(cuenta=cuenta, fin__isnull=True))
        .order_by("nombre")
        .values_list("nombre", flat=True)
        .distinct()
    )
    return ", ".join(nombres) or "ninguna"


def _lineas_objetivo(cuenta):
    return [
        f"Cuenta: {_cuenta(cuenta)}",
        f"Destinatario: {cuenta.destinatario.nombre}",
        f"Terminales con sesión abierta: {_terminales(cuenta)}",
        f"Objetivo: {pesos(cuenta.objetivo)}",
    ]


@_seguro
def cerca_del_objetivo(*, cuenta, antes, despues):
    m = margen()
    if not antes < umbral(cuenta.objetivo, m) <= despues:
        return
    lineas = _lineas_objetivo(cuenta) + [
        f"Margen: {m} %",
        f"Acumulado: {pesos(despues)}",
        f"Acumula desde: {_fecha(cuenta.acumula_desde)}",
        "Prepará el cambio de cuenta en la terminal.",
    ]
    _enviar(f"Cuenta {_cuenta(cuenta)} cerca del objetivo", lineas)


@_seguro
def objetivo_alcanzado(*, cuenta, acumulado):
    lineas = _lineas_objetivo(cuenta) + [
        f"Acumulado: {pesos(acumulado)}",
        f"Acumula desde: {_fecha(cuenta.acumula_desde)}",
        "Cambiá la cuenta en la terminal y creá la sesión nueva.",
    ]
    _enviar(f"Cuenta {_cuenta(cuenta)} alcanzó su objetivo", lineas)


@_seguro
def item_con_falla(item):
    if item.estado_envio == ItemCobro.EstadoEnvio.ERROR:
        asunto = f"Envío a Dentalink con error: cobro {item.cobro_id}"
        dato = f"Error de Dentalink: {item.ultimo_error}"
    elif item.estado_envio == ItemCobro.EstadoEnvio.REVISAR:
        asunto = f"Revisar en Dentalink: cobro {item.cobro_id}"
        dato = "Verificá en Dentalink si el pago se creó antes de reenviarlo."
    else:
        return
    _enviar(
        asunto,
        [
            f"Cobro: {item.cobro_id}",
            f"Ítem: {item.referencia}",
            f"Paciente: {item.cobro.nombre_paciente}",
            f"Prestación: {item.nombre_prestacion}",
            f"Monto: {pesos(item.monto)}",
            dato,
            f"Ver el ítem: {_url('cobros:detalle', item.cobro_id, ancla=f'#item-{item.numero}')}",
        ],
    )


@_seguro
def certificado_por_vencer(*, emisor):
    """Devuelve True si armó el aviso; None si falló (quien llama no marca el aviso como dado)."""
    fecha = localtime(emisor.certificado_vence, ARG).strftime("%d/%m/%Y")
    _enviar(
        f"Certificado de ARCA de {emisor.razon_social} vence el {fecha}",
        [
            f"Emisor: {emisor.razon_social}",
            f"CUIT: {emisor.cuit}",
            f"Vencimiento del certificado: {fecha}",
            "Renová el certificado en ARCA y cargalo en el emisor (manual de facturación ARCA).",
            f"Ver el emisor: {_url('arca:emisor', emisor.pk)}",
        ],
    )
    return True
