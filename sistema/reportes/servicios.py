import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from sistema.arca import servicios as arca
from sistema.cobros.models import Cobro, ComprobanteCobro, ItemCobro, PagoComplementario
from sistema.cobros.servicios import resumen_mes
from sistema.core.models import MontoField
from sistema.core.templatetags.pesos import pesos
from sistema.core.tiempo import ARG, inicio_dia_arg, rango_mes_arg
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import esperado_mensual

logger = logging.getLogger(__name__)
CERO = Decimal("0.00")
MAX_ZIP = 1000
SECCIONES = (
    ("Transferencias", "transferencia"),
    ("Terminal POS", "terminal_pos"),
    ("Efectivo", "efectivo"),
)


def mes_corriente(ahora=None):
    """Día 1 del mes corriente en hora Argentina."""
    return timezone.localtime(ahora or timezone.now(), ARG).date().replace(day=1)


def leer_mes(texto, ahora=None):
    """`"AAAA-MM"` a día 1. Inválido, vacío o posterior al mes corriente: el mes corriente."""
    corriente = mes_corriente(ahora)
    try:
        mes = datetime.strptime(texto, "%Y-%m").date()
    except (TypeError, ValueError):
        return corriente
    return corriente if mes > corriente or mes.year < 2000 else mes


@dataclass
class FilaCuenta:
    cuenta: Cuenta
    nombre: str
    recibido: Decimal
    esperado: Decimal | None


@dataclass
class Seccion:
    nombre: str
    recibido: Decimal
    esperado: Decimal | None
    cuentas: list


@dataclass
class Reporte:
    destinatario: Destinatario
    mes: object
    secciones: list
    complementario: Decimal
    recibido: Decimal
    esperado: Decimal
    diferencia: Decimal


def _esperado(cuenta, destinatario, mes):
    if cuenta.tipo == "efectivo":
        return None
    if cuenta.estado != "activa" or cuenta.destinatario_id != destinatario.pk:
        return CERO
    return (
        esperado_mensual(cuenta, inicio_dia_arg(mes))
        if cuenta.tipo == "transferencia"
        else cuenta.objetivo
    )


def reporte_destinatario(destinatario, mes) -> Reporte:
    """Recibido vs. esperado de un destinatario en `mes`, con detalle por cuenta (FR-001 a FR-005)."""
    total = resumen_mes(mes, destinatarios=Destinatario.objects.filter(pk=destinatario.pk))[0]
    desde, hasta = rango_mes_arg(inicio_dia_arg(mes))
    por_cuenta = {
        f["cuenta"]: f["total"]
        for f in Cobro.objects.filter(
            estado="finalizado", destinatario=destinatario, fecha__gte=desde, fecha__lt=hasta
        )
        .values("cuenta")
        .annotate(total=Sum("monto"))
    }
    cuentas = (
        Cuenta.objects.filter(Q(destinatario=destinatario, estado="activa") | Q(pk__in=por_cuenta))
        .order_by("pk")
        .prefetch_related("topes")
    )
    secciones = []
    for nombre, tipo in SECCIONES:
        filas = [
            FilaCuenta(
                c,
                c.alias or c.titular or c.get_tipo_display(),
                por_cuenta.get(c.pk, CERO),
                _esperado(c, destinatario, mes),
            )
            for c in cuentas
            if c.tipo == tipo
        ]
        if filas:
            esperados = [f.esperado for f in filas if f.esperado is not None]
            secciones.append(
                Seccion(
                    nombre,
                    sum((f.recibido for f in filas), CERO),
                    sum(esperados, CERO) if tipo != "efectivo" else None,
                    filas,
                )
            )
    complementario = PagoComplementario.objects.filter(
        destinatario=destinatario, mes=mes, estado="registrado"
    ).aggregate(t=Coalesce(Sum("monto"), CERO))["t"]
    return Reporte(
        destinatario, mes, secciones, complementario, total.recibido, total.esperado, total.falta
    )


def items_del_mes(id_profesional, mes):
    """Ítems de un profesional en `mes` y su total sin cobros cancelados (FR-017)."""
    if not id_profesional:
        return ItemCobro.objects.none(), CERO
    desde, hasta = rango_mes_arg(inicio_dia_arg(mes))
    items = ItemCobro.objects.filter(
        id_profesional=id_profesional, cobro__fecha__gte=desde, cobro__fecha__lt=hasta
    )
    total = items.exclude(cobro__estado="cancelado").aggregate(t=Coalesce(Sum("monto"), CERO))["t"]
    return items.select_related("cobro").order_by("-cobro__fecha", "numero"), total


def items_por_paciente(id_profesional, q):
    """Ítems de un profesional de todos los meses cuyo paciente contiene `q` (FR-022)."""
    if not id_profesional:
        return ItemCobro.objects.none(), CERO
    items = ItemCobro.objects.filter(
        id_profesional=id_profesional, cobro__nombre_paciente__unaccent__icontains=q
    )
    total = items.exclude(cobro__estado="cancelado").aggregate(t=Coalesce(Sum("monto"), CERO))["t"]
    return items.select_related("cobro").order_by("-cobro__fecha", "numero"), total


TITULOS_DESTINATARIOS = ["Mes", "Destinatario", "Esperado", "Recibido", "Falta", "Cuentas"]


def filas_destinatarios(mes, *, con_falta):
    """Filas del listado de destinatarios del mes (FR-007)."""
    for d in resumen_mes(mes, con_falta=con_falta):
        cuentas = ", ".join(
            f"{c.alias or ''} {c.cbu or ''}".strip() for c in d.cuentas_transferencia
        )
        yield [mes.strftime("%m/%Y"), d.nombre, d.esperado, d.recibido, d.falta, cuentas]


TITULOS_COBRO = [
    "Número", "Fecha", "Estado", "Método de cobro", "Monto", "Bruto", "Neto", "Cuenta", "Alias",
    "CBU o CVU", "Destinatario", "Terminal POS", "Número de operación", "Banco emisor",
    "Últimos 4 dígitos", "Paciente", "Profesionales", "Creador", "Marcas",
    "Motivo de cancelación", "Link al comprobante", "Observaciones",
]  # fmt: skip
TITULOS_ITEM = [("Monto del cobro" if t == "Monto" else t) for t in TITULOS_COBRO] + [
    "Número de ítem", "Tratamiento", "Prestación", "Profesional", "Monto del ítem",
    "Estado de envío",
]  # fmt: skip


def _columnas_cobro(c, url_comprobante):
    cuenta = c.cuenta
    motivo = c.get_motivo_cancelacion_display()
    if c.motivo_cancelacion == "otro":
        motivo = f"Otro: {c.detalle_cancelacion}"
    profesionales = dict.fromkeys(
        i.nombre_profesional for i in c.items.all() if i.nombre_profesional
    )
    marcas = [
        n for n, v in (("Excedente", c.excedente), ("Revisado", c.revisado),
                       ("Sin vincular a Dentalink", c.faltan_items)) if v
    ]  # fmt: skip
    marcas += arca.marcas(c)
    creador = c.creado_por
    return [
        c.pk,
        c.fecha,
        c.get_estado_display(),
        c.get_metodo_display(),
        c.monto,
        c.monto if c.es_pos else None,
        c.monto_neto if c.es_pos else None,
        cuenta.alias or cuenta.titular or cuenta.get_tipo_display(),
        cuenta.alias or "",
        cuenta.cbu or "",
        c.destinatario.nombre if c.destinatario else "",
        c.terminal.nombre if c.terminal else "",
        c.numero_operacion or "",
        c.banco_emisor.nombre if c.banco_emisor else "",
        c.ultimos_4 or "",
        c.nombre_paciente,
        ", ".join(profesionales),
        (creador.get_full_name() or creador.username) if creador else "",
        ", ".join(marcas),
        motivo,
        url_comprobante(c),
        c.observaciones,
    ]


def _cobros(cobros):
    return (
        cobros.select_related("cuenta", "destinatario", "terminal", "banco_emisor", "creado_por")
        .prefetch_related(
            Prefetch("items", ItemCobro.objects.order_by("numero")), "comprobantes_fiscales"
        )
        .order_by("fecha", "pk")
    )


def filas_por_cobro(cobros, *, url_comprobante):
    for c in _cobros(cobros).iterator(chunk_size=2000):  # FR-061: sin armar la lista entera
        yield _columnas_cobro(c, url_comprobante)


def filas_por_item(cobros, *, url_comprobante):
    for c in _cobros(cobros).iterator(chunk_size=2000):
        base = _columnas_cobro(c, url_comprobante)
        items = list(c.items.all())
        if not items:
            yield base + [None] * 6
        for i in items:
            prestacion = "Saldo a favor" if i.saldo_a_favor else i.nombre_prestacion
            yield base + [
                i.referencia, i.nombre_tratamiento, prestacion, i.nombre_profesional, i.monto,
                i.get_estado_envio_display(),
            ]  # fmt: skip


def _nombre_cuenta(cuenta):
    return str(cuenta)


def texto_portapapeles(cobros) -> str:
    """Una línea por cobro y el total sin cancelados al final (FR-009, FR-013)."""
    cobros = list(cobros.select_related("cuenta__destinatario").order_by("fecha", "pk"))
    if not cobros:
        return ""
    cancelado = Cobro.Estado.CANCELADO
    lineas = [
        f"{i}. {timezone.localtime(c.fecha, ARG):%d/%m/%Y %H:%M} - {pesos(c.monto)}"
        f" - {_nombre_cuenta(c.cuenta)} - {c.nombre_paciente}"
        + (" (Cancelado)" if c.estado == cancelado else "")
        for i, c in enumerate(cobros, 1)
    ]
    total = sum((c.monto for c in cobros if c.estado != cancelado), CERO)
    return "\n".join(lineas) + "\n\nTotal sin cancelados " + pesos(total)


def zip_comprobantes(cobros):
    """Zip temporal (posición 0) con los comprobantes de `cobros`. `None` si no hay ninguno."""
    salida = tempfile.TemporaryFile()
    cantidad = 0
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        for c in (
            cobros.filter(Q(~Q(comprobante="")) | Q(otros_comprobantes__isnull=False))
            .distinct()
            .select_related("cuenta")
            .prefetch_related(
                Prefetch("otros_comprobantes", ComprobanteCobro.objects.order_by("pk"))
            )
            .order_by("fecha", "pk")
        ):
            alias = re.sub(r"[^\w.-]", "_", c.cuenta.alias or "") or "sin_alias"
            fecha = timezone.localtime(c.fecha, ARG)
            base = f"{c.pk}_{alias}_{fecha:%d_%m_%Y_%H_%M}"
            archivos = [(c.comprobante, base)] if c.comprobante else []
            # Del 2.º en adelante: sufijo _<n>, con n desde 2 (FR-052).
            archivos += [
                (o.archivo, f"{base}_{n}") for n, o in enumerate(c.otros_comprobantes.all(), 2)
            ]
            for campo, nombre in archivos:
                try:
                    with campo.open("rb") as f:
                        z.writestr(nombre + os.path.splitext(campo.name)[1], f.read())
                except FileNotFoundError:
                    logger.warning("Comprobante faltante en el storage: cobro=%s", c.pk)
                    continue
                cantidad += 1
    if not cantidad:
        salida.close()
        return None
    salida.seek(0)
    return salida


def cobrado(desde: datetime, hasta: datetime) -> Decimal:
    """Suma de Cobro.monto Finalizado con desde <= fecha < hasta (019/FR-013)."""
    return Cobro.objects.filter(
        estado=Cobro.Estado.FINALIZADO, fecha__gte=desde, fecha__lt=hasta
    ).aggregate(t=Coalesce(Sum("monto"), Decimal("0.00"), output_field=MontoField()))["t"]
