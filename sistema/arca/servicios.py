import copy
import logging
from contextlib import contextmanager
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.timezone import localtime

from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import foto, registrar, registrar_alta, registrar_cambio
from sistema.avisos import servicios as avisos
from sistema.cobros.models import Cobro
from sistema.destinatarios.servicios import _guardar

from sistema.core.templatetags.pesos import pesos
from sistema.core.tiempo import ARG
from sistema.usuarios import roles

from . import cliente
from .cliente import Resultado, leer_certificado, validar_clave
from .models import DOCUMENTOS, ComprobanteFiscal, Emisor
from .pdf import LETRA

logger = logging.getLogger(__name__)

ESPERAS = [1, 2, 4, 8, 16]  # minutos después de cada falla (R7)
MAX_INTENTOS = 6
LLAVE_ARCA = 14  # primera llave del advisory lock; la segunda es el pk del emisor (R6)
MINUTOS_COLGADO = 10
MINUTOS_DUDOSO = 2
MARGEN_TICKET = timedelta(minutes=10)
CENTAVO = Decimal("0.01")
CONCEPTO = {"productos": 1, "servicios": 2, "productos_y_servicios": 3}
NOTA_CREDITO_DE = {1: 3, 6: 8, 11: 13}
SIN_CERTIFICADO = "el emisor no tiene certificado o clave"
OTROS_DATOS = "ARCA tiene el comprobante {numero} con otros datos. Revisalo en ARCA."
SIN_RESPUESTA = "ARCA no respondió después de {n} intentos: {error}"
ESPERA_DUDOSO = "Otro comprobante del emisor espera la respuesta de ARCA."


SECRETOS = ("certificado", "clave", "ta_token", "ta_sign")


def _guardar_emisor(emisor, *, certificado=False, clave=False):
    """`_guardar` que no manda a la base lo secreto que no cambió.

    Sentry corre sin scrubbing: si la consulta falla, sus params viajan con el evento. Por eso
    un UPDATE solo lleva la clave o el certificado cuando se reemplazan, ya validados (R3).
    """
    if emisor.pk is None:
        return _guardar(emisor)
    reemplazados = {"certificado": certificado, "ta_token": certificado, "ta_sign": certificado}
    reemplazados["clave"] = clave
    campos = [
        f.name
        for f in emisor._meta.concrete_fields
        if not f.primary_key and (f.name not in SECRETOS or reemplazados[f.name])
    ]
    return _guardar(emisor, campos)


def _pem(archivo) -> str:
    # Sin variable local: el PEM no puede llegar a un traceback (research R3).
    return archivo.read().decode(errors="replace")


def _cargar_archivos(emisor, certificado, clave):
    if certificado is not None:
        emisor.certificado = _pem(certificado)
        emisor.certificado_vence = leer_certificado(emisor.certificado.encode())
    if clave is not None:
        emisor.clave = _pem(clave)
        validar_clave(emisor.clave.encode())


def _revisar_aviso(emisor, ahora=None):
    """Avisa una vez por certificado (R12). Si el aviso falla, `aviso_certificado` sigue en None."""
    if (
        emisor.estado == Emisor.Estado.ACTIVO
        and emisor.aviso_certificado is None
        and emisor.vence_pronto(ahora)
        and avisos.certificado_por_vencer(emisor=emisor)
    ):
        emisor.aviso_certificado = ahora or timezone.now()
        emisor.save(update_fields=["aviso_certificado"])


def estado_certificado(emisor, ahora=None):
    """FR-037. `vencido` (venció antes de hoy), `hoy`, `por_vencer` (aviso de 014) o `None`."""
    if emisor.certificado_vence is None:
        return None
    hoy = localtime(ahora or timezone.now(), ARG).date()
    vence = localtime(emisor.certificado_vence, ARG).date()
    if vence < hoy:
        return "vencido"
    if vence == hoy:
        return "hoy"
    return "por_vencer" if emisor.vence_pronto(ahora) else None


def texto_desactivar(emisor):
    """FR-038: los destinatarios con facturación activa que usan el emisor."""
    nombres = list(
        emisor.destinatarios.filter(facturacion_activa=True)
        .order_by("nombre")
        .values_list("nombre", flat=True)
    )
    if not nombres:
        return "Ningún destinatario lo usa."
    uno = len(nombres) == 1
    return (
        f"{'Lo usa 1 destinatario' if uno else f'Lo usan {len(nombres)} destinatarios'}: "
        f"{', '.join(nombres)}. Sus cobros nuevos no se van a facturar."
    )


def crear_emisor(*, usuario, certificado=None, clave=None, **datos):
    with transaction.atomic():
        emisor = Emisor(**datos)
        if emisor.estado == Emisor.Estado.ACTIVO:
            emisor.fecha_activacion = timezone.now()
        _cargar_archivos(emisor, certificado, clave)
        _guardar(emisor)
        registrar_alta(emisor, usuario=usuario)
        _revisar_aviso(emisor)
        return emisor


def editar_emisor(emisor, *, usuario, certificado=None, clave=None, **datos):
    if "estado" in datos:
        raise ValidationError("El estado se cambia con cambiar_estado_emisor.")
    with transaction.atomic():
        emisor = Emisor.objects.select_for_update().get(pk=emisor.pk)
        antes = foto(emisor)
        for campo, valor in datos.items():
            setattr(emisor, campo, valor)
        _cargar_archivos(emisor, certificado, clave)
        if certificado is not None:
            emisor.aviso_certificado = None
            emisor.ta_token = emisor.ta_sign = ""
            emisor.ta_vence = None
        reemplazos = [
            texto
            for texto, archivo in (("Certificado", certificado), ("Clave", clave))
            if archivo is not None
        ]
        if foto(emisor) == antes and not reemplazos:
            return emisor
        _guardar_emisor(emisor, certificado=certificado is not None, clave=clave is not None)
        # El evento dice qué archivo cambió, nunca su contenido (FR-004).
        detalle = {
            (): "",
            ("Certificado",): "Certificado reemplazado.",
            ("Clave",): "Clave reemplazada.",
        }.get(tuple(reemplazos), "Certificado y clave reemplazados.")
        registrar_cambio(emisor, antes, usuario=usuario, detalle=detalle)
        _revisar_aviso(emisor)
        return emisor


def cambiar_estado_emisor(emisor, *, usuario, estado):
    with transaction.atomic():
        emisor = Emisor.objects.select_for_update().get(pk=emisor.pk)
        if emisor.estado == estado:
            raise ValidationError(f"El emisor ya está {emisor.get_estado_display()}.")
        antes = foto(emisor)
        emisor.estado = estado
        if estado == Emisor.Estado.ACTIVO:
            emisor.fecha_activacion = timezone.now()
        else:
            emisor.fecha_archivado = timezone.now()
        _guardar_emisor(emisor)
        registrar_cambio(emisor, antes, usuario=usuario)
        _revisar_aviso(emisor)
        return emisor


def avisar_certificados(*, ahora=None):
    """Revisa todos los emisores Activos (R12). Corre una vez por día."""
    ids = Emisor.objects.filter(estado=Emisor.Estado.ACTIVO).values_list("pk", flat=True)
    for pk in list(ids):
        with transaction.atomic():
            _revisar_aviso(Emisor.objects.select_for_update().get(pk=pk), ahora)


def facturable(cobro) -> bool:
    """FR-008 y FR-031: Finalizado, con ítems, no migrado y de un destinatario con facturación."""
    destinatario = cobro.destinatario
    return (
        cobro.estado == Cobro.Estado.FINALIZADO
        and not cobro.faltan_items
        and not cobro.migrado
        and destinatario is not None
        and destinatario.facturacion_activa
    )


# Misma condición que `facturable`, como filtro de base (020/FR-018). Un test las compara.
FACTURABLE_Q = Q(
    estado=Cobro.Estado.FINALIZADO,
    faltan_items=False,
    migrado=False,
    destinatario__facturacion_activa=True,
)


def sin_factura_vigente(cobro) -> bool:
    """020/FR-018: Finalizado, facturable y sin ninguna factura vigente."""
    return (
        facturable(cobro)
        and not cobro.comprobantes_fiscales.filter(
            tipo=ComprobanteFiscal.Tipo.FACTURA, vigente=True
        ).exists()
    )


def factura_vigente(cobro):
    return cobro.comprobantes_fiscales.filter(tipo="factura", vigente=True).first()


def _encolar(comprobante):
    from . import tasks  # tasks.py importa servicios.py

    pk = comprobante.pk
    transaction.on_commit(lambda: tasks.emitir.delay(pk), robust=True)


def _nueva_factura(cobro, *, usuario=None, origen="facturación automática", datos=None):
    """Factura Pendiente del emisor del destinatario del cobro. `datos` pisa campos (US4)."""
    emisor = cobro.destinatario.emisor
    comprobante = ComprobanteFiscal(
        **{
            "cobro": cobro,
            "emisor": emisor,
            "tipo": ComprobanteFiscal.Tipo.FACTURA,
            "estado": ComprobanteFiscal.Estado.PENDIENTE,
            "concepto": emisor.concepto,
            "descripcion": emisor.descripcion,
            **(datos or {}),
        }
    )
    comprobante.full_clean()
    comprobante.save()
    registrar_alta(comprobante, usuario=usuario, origen=origen)
    _encolar(comprobante)
    return comprobante


def al_finalizar(cobro):
    """Crea la factura Pendiente del cobro. Nunca deshace el cobro (FR-023): falla en savepoint."""
    try:
        with transaction.atomic():
            # FR-013: dos llamadas a la vez van en fila sobre el cobro.
            cobro = Cobro.objects.select_for_update().get(pk=cobro.pk)
            if not facturable(cobro) or factura_vigente(cobro) is not None:
                return None
            return _nueva_factura(cobro)
    except Exception:
        logger.exception("Factura no creada: cobro=%s", cobro.pk)
        return None


def importes(comprobante, emisor):
    """(neto, iva, total) de R10. La nota de crédito repite los de su factura."""
    if comprobante.tipo == ComprobanteFiscal.Tipo.NOTA_CREDITO:
        factura = comprobante.asociado
        return factura.neto, factura.iva, factura.total
    total = comprobante.cobro.monto
    if emisor.tipo_comprobante == Emisor.TipoComprobante.C:
        return total, Decimal("0.00"), total
    neto = (total / (1 + Decimal(emisor.alicuota_iva) / 100)).quantize(CENTAVO, ROUND_HALF_UP)
    return neto, total - neto, total


def _receptor(comprobante):
    """(nombre, doc_tipo, doc_numero) de R10. Lo cargado en la factura gana sobre el cobro."""
    cobro = comprobante.cobro
    if comprobante.doc_tipo is not None:
        tipo, numero = comprobante.doc_tipo, comprobante.doc_numero or "0"
    else:
        digitos = "".join(c for c in cobro.dni_paciente if c in "0123456789")
        valido = digitos.strip("0") != "" and len(digitos) <= 11
        tipo, numero = (96, digitos) if valido else (99, "0")
    nombre = comprobante.receptor_nombre or cobro.nombre_paciente or "Consumidor final"
    return nombre, tipo, numero


def _cbte_tipo(comprobante, emisor):
    if comprobante.tipo == ComprobanteFiscal.Tipo.NOTA_CREDITO:
        return NOTA_CREDITO_DE[comprobante.asociado.cbte_tipo]
    return emisor.codigo_factura


def armar_pedido(comprobante, numero, hoy):
    """El `pedido` de `cliente.solicitar_cae` (contrato). `hoy` es la fecha ARG de emisión."""
    emisor, asociado = comprobante.emisor, comprobante.asociado
    neto, iva, total = importes(comprobante, emisor)
    _, doc_tipo, doc_numero = _receptor(asociado or comprobante)
    del_cobro = localtime(comprobante.cobro.fecha, ARG).date()
    factura = None
    if asociado is not None:
        factura = {
            "tipo": asociado.cbte_tipo,
            "punto_venta": asociado.punto_venta,
            "numero": asociado.numero,
            "cuit": emisor.cuit,
            "fecha": asociado.fecha,
        }
    return {
        "cbte_tipo": _cbte_tipo(comprobante, emisor),
        "punto_venta": emisor.punto_venta,
        "numero": numero,
        "concepto": CONCEPTO[comprobante.concepto],
        "doc_tipo": doc_tipo,
        "doc_numero": doc_numero,
        "fecha": hoy,
        "neto": neto,
        "iva": iva,
        "total": total,
        "alicuota_id": None
        if emisor.tipo_comprobante == Emisor.TipoComprobante.C
        else Emisor.ALICUOTA_ID[emisor.alicuota_iva],
        "servicio_desde": comprobante.servicio_desde or del_cobro,
        "servicio_hasta": comprobante.servicio_hasta or del_cobro,
        "vto_pago": hoy,
        "asociado": factura,
    }


def _ticket(emisor, ahora) -> Resultado:
    """Ticket de WSAA (R4): el guardado si le quedan más de 10 minutos; si no, uno nuevo."""
    vence = emisor.ta_vence
    if emisor.ta_token and emisor.ta_sign and vence is not None and vence > ahora + MARGEN_TICKET:
        return Resultado("ok", {"token": emisor.ta_token, "sign": emisor.ta_sign})
    resultado = cliente.login(emisor, ahora=ahora)
    if resultado.tipo == "ok":
        with transaction.atomic():
            Emisor.objects.filter(pk=emisor.pk).update(
                ta_token=resultado.datos["token"],
                ta_sign=resultado.datos["sign"],
                ta_vence=resultado.datos["vence"],
            )
    return resultado


def _borrar_ticket(emisor_id):
    Emisor.objects.filter(pk=emisor_id).update(ta_token="", ta_sign="", ta_vence=None)


@contextmanager
def _fila_emisor(emisor_id):
    """Un solo emisor a la vez por emisor (R6). Fuera de transacción: se suelta en `finally`."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, %s)", [LLAVE_ARCA, emisor_id])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [LLAVE_ARCA, emisor_id])


def _cambios(antes, comprobante):
    despues = foto(comprobante)
    return [
        {"campo": k, "anterior": antes.get(k), "nuevo": despues.get(k)}
        for k in sorted(antes | despues)
        if antes.get(k) != despues.get(k)
    ]


def _evento(comprobante, antes, detalle, exito):
    registrar(
        Evento.Tipo.EMISION_ARCA,
        usuario=None,
        entidad=comprobante,
        cambios=_cambios(antes, comprobante),
        origen="emisión automática",
        detalle=detalle,
        exito=exito,
    )


def _chequeo_local(comprobante, ahora):
    """R7: motivo si no corresponde llamar a ARCA, en el orden de la spec; si no, None."""
    emisor = comprobante.emisor
    if not (emisor.certificado and emisor.clave):
        return SIN_CERTIFICADO
    if comprobante.tipo == ComprobanteFiscal.Tipo.FACTURA and emisor.estado != Emisor.Estado.ACTIVO:
        return "emisor inactivo"
    if emisor.certificado_vence is not None and emisor.certificado_vence <= ahora:
        return "certificado vencido"
    if emisor.ambiente == Emisor.Ambiente.PRODUCCION and not settings.ARCA_PRODUCCION:
        return cliente.SIN_PRODUCCION
    return None


def _dudoso_reciente(comprobante, ahora):
    """Otro comprobante del emisor con número enviado y sin CAE, en curso o de hace <2 minutos."""
    return (
        ComprobanteFiscal.objects.filter(
            emisor_id=comprobante.emisor_id,
            estado=ComprobanteFiscal.Estado.PENDIENTE,
            numero_intentado__isnull=False,
            cae="",
        )
        .exclude(pk=comprobante.pk)
        .filter(
            Q(en_curso_desde__isnull=False)
            | Q(fecha_cambio__gt=ahora - timedelta(minutes=MINUTOS_DUDOSO))
        )
        .exists()
    )


def _coincide(dudoso, datos):
    """ARCA tiene el número con el mismo documento y el mismo total que enviamos."""
    _, _, doc_numero = _receptor(dudoso.asociado or dudoso)
    try:
        mismo_doc = int(datos["doc_numero"]) == int(doc_numero)
    except ValueError:
        return False
    return mismo_doc and datos["total"] == dudoso.total


def _soltar_numero(pk):
    """ARCA no tiene el número intentado: queda libre. Nunca se reusa sin haberlo consultado."""
    with transaction.atomic():
        c = ComprobanteFiscal.objects.select_for_update().get(pk=pk)
        antes = foto(c)
        c.numero_intentado = None
        _guardar(c)
        _evento(c, antes, "Consulta: ARCA no tiene el número intentado.", None)


def _resolver_dudosos(comprobante, emisor, ticket, ahora):
    """Consulta cada número enviado sin respuesta. Un veredicto sobre `comprobante` lo corta."""
    dudosos = ComprobanteFiscal.objects.select_related("cobro", "asociado").filter(
        emisor=emisor,
        estado=ComprobanteFiscal.Estado.PENDIENTE,
        numero_intentado__isnull=False,
        cae="",
    )
    for dudoso in list(dudosos.order_by("pk")):
        numero = dudoso.numero_intentado
        consulta = cliente.consultar(emisor, ticket, dudoso.cbte_tipo, numero)
        if consulta.tipo == "rechazado":
            _soltar_numero(dudoso.pk)
            continue
        if consulta.tipo != "ok":
            return consulta, None, True  # sin red: corta el intento, nunca se emite otro número
        if _coincide(dudoso, consulta.datos):
            veredicto = Resultado("ok", consulta.datos)
        else:
            veredicto = Resultado("rechazado", error=OTROS_DATOS.format(numero=numero))
        if dudoso.pk == comprobante.pk:
            return veredicto, numero, False
        _cerrar(dudoso.pk, ahora, veredicto, numero, False)
    return None


def _intentar(pk, ahora):
    """Paso 2 de R6, con la fila del emisor. Devuelve `(Resultado, numero, conservar_numero)`."""
    c = ComprobanteFiscal.objects.select_related("emisor", "cobro", "asociado").get(pk=pk)
    if c.estado != ComprobanteFiscal.Estado.PENDIENTE:
        return Resultado("omitido"), None, True  # otro intento lo resolvió mientras esperábamos
    if _dudoso_reciente(c, ahora):
        # Otro quedó dudoso mientras esperábamos la fila: ARCA puede seguir procesándolo y la
        # consulta daría "no existe". Se espera como en el paso 1 (R6).
        return Resultado("reintentable", error=ESPERA_DUDOSO), None, True
    emisor = c.emisor
    ticket = _ticket(emisor, ahora)
    if ticket.tipo != "ok":
        return ticket, None, True
    corte = _resolver_dudosos(c, emisor, ticket.datos, ahora)
    if corte is not None:
        return corte
    cbte_tipo = _cbte_tipo(c, emisor)
    ultimo = cliente.ultimo_autorizado(emisor, ticket.datos, cbte_tipo)
    if ultimo.tipo != "ok":
        return ultimo, None, True
    numero = ultimo.datos["numero"] + 1
    with transaction.atomic():
        c = (
            ComprobanteFiscal.objects.select_for_update(of=("self",))
            .select_related("cobro", "asociado")
            .get(pk=pk)
        )
        if c.estado != ComprobanteFiscal.Estado.PENDIENTE:
            return Resultado("omitido"), None, True
        c.numero_intentado, c.cbte_tipo, c.punto_venta = numero, cbte_tipo, emisor.punto_venta
        c.neto, c.iva, c.total = importes(c, emisor)
        c.receptor_nombre, c.doc_tipo, c.doc_numero = _receptor(c.asociado or c)
        _guardar(c)
    resultado = cliente.solicitar_cae(
        emisor, ticket.datos, armar_pedido(c, numero, localtime(ahora, ARG).date())
    )
    return resultado, numero, resultado.tipo == "dudoso"


def _encolar_facturas_en_espera(cobro_id):
    """La nota de crédito quedó Emitida: la factura nueva Pendiente del cobro ya puede salir."""
    for factura in ComprobanteFiscal.objects.filter(
        cobro_id=cobro_id,
        tipo=ComprobanteFiscal.Tipo.FACTURA,
        vigente=True,
        estado=ComprobanteFiscal.Estado.PENDIENTE,
    ):
        _encolar(factura)


def _cerrar(pk, ahora, resultado, numero, conservar_numero, antes=None):
    """Paso 3 de R6: guarda el veredicto y deja el evento (FR-028). Devuelve el estado."""
    Estado = ComprobanteFiscal.Estado
    with transaction.atomic():
        c = ComprobanteFiscal.objects.select_for_update().get(pk=pk)
        antes = antes or foto(c)
        c.en_curso_desde = None
        if c.estado != Estado.PENDIENTE and resultado.tipo != "ok":
            _guardar(c)  # otro proceso lo resolvió: no se pisa
            return c.estado
        if resultado.tipo == "ok":
            # Un CAE de ARCA se guarda siempre, aunque el comprobante haya cambiado mientras tanto.
            datos = resultado.datos
            c.estado, c.numero = Estado.EMITIDO, numero
            c.cae, c.cae_vence = datos["cae"], datos["cae_vence"]
            c.fecha = datos.get("fecha") or localtime(ahora, ARG).date()
            c.numero_intentado, c.motivo, c.proximo_intento = None, "", None
            if c.tipo == ComprobanteFiscal.Tipo.NOTA_CREDITO:
                _encolar_facturas_en_espera(c.cobro_id)
        elif resultado.tipo == "rechazado":
            c.estado, c.proximo_intento, c.numero_intentado = Estado.ERROR, None, None
            c.motivo = resultado.error or "ARCA rechazó el comprobante."
        else:  # reintentable o dudoso
            if isinstance(resultado.datos, dict) and resultado.datos.get("token_invalido"):
                _borrar_ticket(c.emisor_id)
            if not conservar_numero:
                c.numero_intentado = None
            if c.intentos >= MAX_INTENTOS:
                c.estado, c.proximo_intento = Estado.ERROR, None
                c.motivo = SIN_RESPUESTA.format(n=MAX_INTENTOS, error=resultado.error)
            else:
                c.proximo_intento = ahora + timedelta(minutes=ESPERAS[c.intentos - 1])
        _guardar(c)
        _evento(
            c,
            antes,
            f"Intento {c.intentos}: {resultado.tipo}. {resultado.error}",
            resultado.tipo == "ok",
        )
        return c.estado


def _espera_nota_credito(c):
    """SC-002: la factura nueva no sale hasta que la nota de crédito del cobro esté Emitida."""
    Estado = ComprobanteFiscal.Estado
    return (
        c.tipo == ComprobanteFiscal.Tipo.FACTURA
        and ComprobanteFiscal.objects.filter(cobro_id=c.cobro_id, tipo="nota_credito")
        .exclude(estado__in=[Estado.EMITIDO, Estado.DESCARTADO])
        .exists()
    )


def emitir(comprobante_id, *, ahora=None) -> str:
    """Un intento de emisión con los 3 pasos de R6. Devuelve el estado final o `"omitido"`."""
    ahora = ahora or timezone.now()
    Estado = ComprobanteFiscal.Estado
    with transaction.atomic():  # paso 1
        c = (
            ComprobanteFiscal.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("emisor")
            .filter(pk=comprobante_id)
            .first()
        )
        if (
            c is None
            or c.estado != Estado.PENDIENTE
            or c.en_curso_desde is not None
            or (c.proximo_intento is not None and c.proximo_intento > ahora)
            or _dudoso_reciente(c, ahora)
            or _espera_nota_credito(c)
        ):
            return "omitido"
        antes = foto(c)
        motivo = _chequeo_local(c, ahora)
        if motivo:
            c.estado, c.motivo, c.proximo_intento = Estado.ERROR, motivo, None
            _guardar(c)
            _evento(c, antes, f"Chequeo local: rechazado. {motivo}", False)
            return c.estado
        c.intentos += 1
        c.en_curso_desde = ahora
        _guardar(c)
    with _fila_emisor(c.emisor_id):  # pasos 2 y 3
        resultado, numero, conservar = _intentar(comprobante_id, ahora)
        return _cerrar(comprobante_id, ahora, resultado, numero, conservar, antes)


def liberar_colgados(*, ahora=None) -> int:
    """R6: una emisión en curso hace más de 10 minutos vuelve a la cola, con su `numero_intentado`."""
    ahora = ahora or timezone.now()
    limite = ahora - timedelta(minutes=MINUTOS_COLGADO)
    liberados = 0
    with transaction.atomic():
        colgados = ComprobanteFiscal.objects.select_for_update(skip_locked=True).filter(
            en_curso_desde__lt=limite
        )
        for c in colgados:
            antes = foto(c)
            c.en_curso_desde = None
            if c.estado == ComprobanteFiscal.Estado.PENDIENTE and c.intentos >= MAX_INTENTOS:
                c.estado, c.proximo_intento = ComprobanteFiscal.Estado.ERROR, None
                c.motivo = SIN_RESPUESTA.format(n=MAX_INTENTOS, error="la emisión quedó colgada")
            _guardar(c)
            _evento(c, antes, "La emisión quedó colgada y se liberó.", False)
            liberados += 1
    return liberados


class FaltaDecision(ValidationError):
    """El cobro facturado cambia y falta la respuesta sobre la nota de crédito (FR-014)."""

    def __init__(self, pregunta):
        super().__init__(pregunta)
        self.pregunta = pregunta


def factura_emitida(cobro):
    """La factura vigente del cobro si ya está Emitida; si no, None."""
    factura = factura_vigente(cobro)
    if factura is not None and factura.estado == ComprobanteFiscal.Estado.EMITIDO:
        return factura
    return None


def _nota_credito(factura, *, usuario, origen="", nueva=False, datos=None, detalle=""):
    """Nota de crédito Pendiente de una factura Emitida; con `nueva`, también la factura nueva."""
    antes = foto(factura)
    factura.vigente = factura.a_revisar = False
    _guardar(factura)
    registrar_cambio(factura, antes, usuario=usuario, origen=origen, detalle=detalle)
    nota = ComprobanteFiscal(
        cobro=factura.cobro,
        emisor=factura.emisor,
        tipo=ComprobanteFiscal.Tipo.NOTA_CREDITO,
        asociado=factura,
        estado=ComprobanteFiscal.Estado.PENDIENTE,
        concepto=factura.concepto,
        descripcion=factura.descripcion,
        receptor_nombre=factura.receptor_nombre,
        doc_tipo=factura.doc_tipo,
        doc_numero=factura.doc_numero,
        servicio_desde=factura.servicio_desde,
        servicio_hasta=factura.servicio_hasta,
    )
    _guardar(nota)
    registrar_alta(nota, usuario=usuario, origen=origen)
    _encolar(nota)
    if nueva and facturable(factura.cobro):
        # Queda Pendiente, pero `emitir` la espera hasta que la nota de crédito esté Emitida.
        _nueva_factura(factura.cobro, usuario=usuario, origen=origen, datos=datos)
    return nota


def _error_sin_confirmar(factura, accion):
    """La factura está en curso o con un número enviado sin respuesta: no se toca (R9)."""
    if factura.estado == ComprobanteFiscal.Estado.ERROR:
        return ValidationError(
            f"La factura del cobro quedó sin confirmar en ARCA. Reintentala antes de {accion}."
        )
    return ValidationError("La factura del cobro se está emitiendo. Probá de nuevo en un minuto.")


def _pregunta(factura, cobro, cancelado):
    """020/FR-018: la pregunta dice qué va a pasar con la factura nueva."""
    texto = f"¿Emitir nota de crédito por {pesos(factura.total)}"
    if cancelado:
        return texto + "?"
    if facturable(cobro):
        return texto + " y una factura nueva?"
    return texto + "? No se emite factura nueva: el destinatario no tiene la facturación activa."


def _marcar_a_revisar(factura, *, usuario, origen):
    if factura.a_revisar:
        return
    antes = foto(factura)
    factura.a_revisar = True
    _guardar(factura)
    registrar_cambio(factura, antes, usuario=usuario, origen=origen)


def _descartar_o_mover(factura, cobro, *, cancelado, destinatario_cambia, usuario, origen):
    """Factura sin emitir (R9): se descarta o toma el emisor del destinatario nuevo."""
    antes = foto(factura)
    if cancelado or not facturable(cobro):
        factura.estado, factura.vigente, factura.a_revisar = (
            ComprobanteFiscal.Estado.DESCARTADO,
            False,
            False,
        )
        factura.proximo_intento = None
    elif destinatario_cambia and cobro.destinatario.emisor_id != factura.emisor_id:
        factura.emisor = cobro.destinatario.emisor
    else:
        return
    _guardar(factura)
    registrar_cambio(factura, antes, usuario=usuario, origen=origen)


def al_cambiar_cobro(cobro, antes, *, usuario, cancelado=False, nota_credito=None, origen=""):
    """R9. `antes` es `foto(cobro)` previa. `usuario=None` es el sistema: nunca pregunta."""
    destinatario_cambia = cobro.destinatario_id != antes["destinatario_id"]
    if not (cancelado or destinatario_cambia or cobro.monto != antes["monto"]):
        return  # FR-016: observaciones, fecha u otros campos no tocan la factura
    factura = (
        ComprobanteFiscal.objects.select_for_update(of=("self",))
        .filter(cobro=cobro, tipo=ComprobanteFiscal.Tipo.FACTURA, vigente=True)
        .first()
    )
    if factura is None:
        return
    Estado = ComprobanteFiscal.Estado
    if factura.estado == Estado.EMITIDO:
        if usuario is None:
            if destinatario_cambia:
                _marcar_a_revisar(factura, usuario=None, origen=origen)
            return
        if nota_credito is None:
            raise FaltaDecision(_pregunta(factura, cobro, cancelado))
        detalle = "Nota de crédito: sí" if nota_credito else "Nota de crédito: no"
        if nota_credito:
            _nota_credito(
                factura, usuario=usuario, origen=origen, nueva=not cancelado, detalle=detalle
            )
        else:
            registrar_cambio(
                factura, foto(factura), usuario=usuario, origen=origen, detalle=detalle
            )
        return
    dudosa = factura.numero_intentado is not None
    if factura.en_curso or dudosa:
        if usuario is None:
            if destinatario_cambia:
                _marcar_a_revisar(factura, usuario=None, origen=origen)
            return
        raise _error_sin_confirmar(factura, "cambiar el cobro")
    if factura.estado in (Estado.PENDIENTE, Estado.ERROR):
        _descartar_o_mover(
            factura,
            cobro,
            cancelado=cancelado,
            destinatario_cambia=destinatario_cambia,
            usuario=usuario,
            origen=origen,
        )


def _exigir_admin(usuario):
    if usuario.role != roles.ADMIN:
        raise PermissionDenied


def _factura_bloqueada(cobro):
    """Bloquea el cobro y devuelve su factura vigente (o None) en este orden, como 007."""
    cobro = Cobro.objects.select_for_update().get(pk=cobro.pk)
    factura = (
        ComprobanteFiscal.objects.select_for_update(of=("self",))
        .select_related("cobro__destinatario", "emisor")
        .filter(cobro=cobro, tipo=ComprobanteFiscal.Tipo.FACTURA, vigente=True)
        .first()
    )
    return cobro, factura


def emitir_nota_credito(cobro, *, usuario):
    """FR-018: nota de crédito de la factura vigente Emitida, sin importar el estado del cobro."""
    _exigir_admin(usuario)
    with transaction.atomic():
        _, factura = _factura_bloqueada(cobro)
        if factura is None or factura.estado != ComprobanteFiscal.Estado.EMITIDO:
            raise ValidationError("El cobro no tiene una factura emitida vigente.")
        return _nota_credito(factura, usuario=usuario, detalle="Nota de crédito: sí")


def resolver_a_revisar(cobro, *, usuario, emitir):
    """FR-015: el administrador decide sobre una factura marcada «a revisar»."""
    _exigir_admin(usuario)
    with transaction.atomic():
        _, factura = _factura_bloqueada(cobro)
        if factura is None or not factura.a_revisar:
            raise ValidationError("El cobro no tiene una factura a revisar.")
        if not emitir:
            antes = foto(factura)
            factura.a_revisar = False
            _guardar(factura)
            registrar_cambio(factura, antes, usuario=usuario, detalle="Nota de crédito: no")
            return None
        if factura.estado != ComprobanteFiscal.Estado.EMITIDO:
            raise ValidationError("La factura todavía no está emitida. Probá de nuevo más tarde.")
        return _nota_credito(factura, usuario=usuario, nueva=True, detalle="Nota de crédito: sí")


def reintentar(comprobante, *, usuario):
    """FR-027: Error → Pendiente. `numero_intentado` no cambia: el intento lo consulta primero."""
    _exigir_admin(usuario)
    with transaction.atomic():
        c = ComprobanteFiscal.objects.select_for_update().get(pk=comprobante.pk)
        if c.estado != ComprobanteFiscal.Estado.ERROR:
            raise ValidationError("Solo se reintenta un comprobante fiscal con error.")
        # 020/FR-017: una factura reintenta con el emisor actual del destinatario. Una nota de
        # crédito sigue con el emisor de su factura, y un número enviado sin respuesta se consulta
        # con el emisor que lo envió: cambiarlo puede emitir dos veces (R6).
        factura = c.tipo == ComprobanteFiscal.Tipo.FACTURA
        emisor = c.emisor
        if factura and c.numero_intentado is None:
            emisor = getattr(c.cobro.destinatario, "emisor", None) or c.emisor
        if factura and emisor.estado != Emisor.Estado.ACTIVO:
            raise ValidationError(
                f"No se puede reintentar: el emisor «{emisor.razon_social}» está Inactivo."
            )
        if emisor.certificado_vence is not None and emisor.certificado_vence <= timezone.now():
            vence = localtime(emisor.certificado_vence, ARG).strftime("%d/%m/%Y")
            raise ValidationError(
                f"No se puede reintentar: el certificado del emisor venció el {vence}."
            )
        antes = foto(c)
        c.emisor = emisor
        c.estado, c.intentos, c.motivo = ComprobanteFiscal.Estado.PENDIENTE, 0, ""
        c.proximo_intento = c.en_curso_desde = None
        _guardar(c)
        registrar_cambio(c, antes, usuario=usuario, detalle="Reintento pedido")
        _encolar(c)
    return c


def marcas(cobro) -> list[str]:
    """FR-027. Usa `comprobantes_fiscales.all()`: con `prefetch_related` no hace consultas."""
    comprobantes = list(cobro.comprobantes_fiscales.all())
    resultado = []
    if any(c.estado == ComprobanteFiscal.Estado.ERROR for c in comprobantes):
        resultado.append("Factura con error")
    if any(
        c.tipo == ComprobanteFiscal.Tipo.FACTURA and c.vigente and c.a_revisar for c in comprobantes
    ):
        resultado.append("Factura a revisar")
    return resultado


CAMPOS_DATOS = (
    "concepto",
    "descripcion",
    "receptor_nombre",
    "doc_tipo",
    "doc_numero",
    "servicio_desde",
    "servicio_hasta",
)


def pregunta_datos(factura):
    return "ARCA no permite editar una factura emitida. " + _pregunta(
        factura, factura.cobro, cancelado=False
    )


def editar_datos_factura(cobro, *, usuario, nota_credito=None, **datos):
    """FR-020 a FR-022. Sin emitir: cambia la factura. Emitida: pregunta por la nota de crédito."""
    _exigir_admin(usuario)
    for campo in datos:
        if campo not in CAMPOS_DATOS:
            raise ValidationError(f"El campo {campo} no se edita.")
    with transaction.atomic():
        _, factura = _factura_bloqueada(cobro)
        if factura is None:
            raise ValidationError("El cobro no tiene una factura vigente.")
        if factura.numero_intentado is not None or factura.en_curso:
            raise _error_sin_confirmar(factura, "cambiar sus datos")
        antes = foto(factura)
        nueva = copy.copy(factura)  # se valida antes de preguntar o de guardar
        for campo, valor in datos.items():
            setattr(nueva, campo, valor)
        nueva.full_clean()
        if foto(nueva) == antes:
            return factura
        if factura.estado == ComprobanteFiscal.Estado.EMITIDO:
            if nota_credito is None:
                raise FaltaDecision(pregunta_datos(factura))
            if not nota_credito:
                registrar_cambio(factura, antes, usuario=usuario, detalle="Nota de crédito: no")
                return factura
            _nota_credito(
                factura, usuario=usuario, nueva=True, datos=datos, detalle="Nota de crédito: sí"
            )
            return factura_vigente(factura.cobro)
        _guardar(nueva)
        registrar_cambio(nueva, antes, usuario=usuario)
        return nueva


TITULOS_LIBRO = [
    "Fecha", "Tipo", "Punto de venta", "Número", "Receptor", "Documento",
    "Neto", "IVA", "Total", "CAE",
]  # fmt: skip


def libro_iva(emisor, mes):
    """R11: (filas, totales) de los Emitidos del emisor en el mes (`mes` es el día 1, fecha ARG).
    La nota de crédito resta. `totales` es (neto, iva, total)."""
    siguiente = (mes.replace(day=28) + timedelta(days=4)).replace(day=1)
    emitidos = ComprobanteFiscal.objects.filter(
        emisor=emisor,
        estado=ComprobanteFiscal.Estado.EMITIDO,
        fecha__gte=mes,
        fecha__lt=siguiente,
    ).order_by("fecha", "cbte_tipo", "numero")
    filas = []
    for c in emitidos:
        signo = -1 if c.tipo == ComprobanteFiscal.Tipo.NOTA_CREDITO else 1
        documento = DOCUMENTOS.get(c.doc_tipo, "")
        if c.doc_numero not in ("", "0"):
            documento = f"{documento} {c.doc_numero}".strip()
        filas.append(
            [
                c.fecha,
                f"{c.get_tipo_display()} {LETRA[c.cbte_tipo]}",
                c.punto_venta,
                c.numero,
                c.receptor_nombre,
                documento,
                signo * c.neto,
                signo * c.iva,
                signo * c.total,
                c.cae,
            ]
        )
    totales = tuple(sum((f[i] for f in filas), Decimal("0.00")) for i in (6, 7, 8))
    return filas, totales
