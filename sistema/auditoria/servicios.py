from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.dateformat import format as formato_fecha
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import capfirst
from django.utils.timezone import localtime

from sistema.auditoria.middleware import contexto_actual
from sistema.auditoria.models import Evento
from sistema.core.templatetags.pesos import pesos

MAX_FALLOS = 5
DURACION_BLOQUEO = timedelta(minutes=15)
BLOQUEADO = "Usuario bloqueado"

EXCLUIDOS = {
    "password",
    "last_login",
    "fecha_cambio",
    "certificado",
    "clave",
    "ta_token",
    "ta_sign",
    "ta_vence",
}

ENTIDADES = {
    "destinatarios.destinatario": "Destinatario",
    "destinatarios.cuenta": "Cuenta",
    "destinatarios.proveedor": "Proveedor",
    "terminales.terminal": "Terminal POS",
    "terminales.sesion": "Sesión",
    "cobros.cobro": "Cobro",
    "cobros.itemcobro": "Ítem de cobro",
    "cobros.comprobantecobro": "Comprobante de cobro",
    "cobros.pagocomplementario": "Pago complementario",
    "dentalink.configuraciondentalink": "Configuración de Dentalink",
    "dentalink.banco": "Banco de Dentalink",
    "dentalink.profesional": "Profesional de Dentalink",
    "dentalink.mediopago": "Medio de pago de Dentalink",
    "avisos.configuracionavisos": "Configuración de avisos",
    "avisos.direccionaviso": "Dirección de aviso",
    "automatizacion.regla": "Regla de automatización",
    "arca.emisor": "Emisor",
    "arca.comprobantefiscal": "Comprobante fiscal",
    "payment_instructions.user": "Usuario",
}

# Rótulos con tildes, los mismos que usan los formularios. Sin entrada: capfirst(verbose_name).
ROTULOS = {
    "fecha_activacion": "Fecha de activación",
    "fecha_archivado": "Fecha de archivado",
    "fecha_alta": "Fecha de alta",
    "usuario_profesional_id": "Usuario Profesional",
    "facturacion_activa": "Facturación activa",
    "emisor_id": "Emisor",
    "telefono": "Teléfono",
    "cuit": "CUIT",
    "titular_cuit": "CUIT o CUIL del titular",
    "cbu": "CBU",
    "minimo_por_pago": "Mínimo por pago",
    "posicion": "Posición",
    "acumula_desde": "Acumula desde",
    "id_paciente_dentalink": "Id del paciente en Dentalink",
    "nombre_paciente": "Nombre del paciente",
    "dni_paciente": "DNI del paciente",
    "faltan_items": "Faltan ítems",
    "banco_emisor_id": "Banco emisor",
    "creado_por_id": "Creado por",
    "destinatario_id": "Destinatario",
    "metodo": "Método",
    "numero_operacion": "Número de operación",
    "motivo_cancelacion": "Motivo de cancelación",
    "detalle_cancelacion": "Detalle de la cancelación",
    "comentario_revision": "Comentario de la revisión",
    "motivo_anulacion": "Motivo de anulación",
    "detalle_anulacion": "Detalle de la anulación",
    "numero": "Número",
    "id_tratamiento": "Id del tratamiento",
    "id_prestacion": "Id de la prestación",
    "id_profesional": "Id del profesional",
    "nombre_tratamiento": "Nombre del tratamiento",
    "nombre_prestacion": "Nombre de la prestación",
    "nombre_profesional": "Nombre del profesional",
    "estado_envio": "Estado del envío",
    "id_pago_dentalink": "Id del pago en Dentalink",
    "proximo_intento": "Próximo intento",
    "en_curso_desde": "En curso desde",
    "ultimo_error": "Último error",
    "id_prestacion_saldo_a_favor": "Id de la prestación saldo a favor",
    "nombre_prestacion_saldo_a_favor": "Nombre de la prestación saldo a favor",
    "banco_por_defecto_id": "Banco por defecto",
    "id_dentalink": "Id en Dentalink",
    "razon_social": "Razón social",
    "condicion_iva": "Condición frente al IVA",
    "punto_venta": "Punto de venta",
    "tipo_comprobante": "Tipo de comprobante",
    "alicuota_iva": "Alícuota de IVA",
    "descripcion": "Descripción",
    "certificado_vence": "Vencimiento del certificado",
    "aviso_certificado": "Aviso del vencimiento",
    "cobro_id": "Cobro",
    "asociado_id": "Comprobante asociado",
    "a_revisar": "A revisar",
    "cbte_tipo": "Tipo de comprobante",
    "numero_intentado": "Número intentado",
    "receptor_nombre": "Nombre del receptor",
    "doc_tipo": "Tipo de documento",
    "doc_numero": "Número de documento",
    "servicio_desde": "Servicio desde",
    "servicio_hasta": "Servicio hasta",
    "cae_vence": "Vencimiento del CAE",
    "cae": "CAE",
    "role": "Rol",
    "username": "Usuario",
    "first_name": "Nombre",
    "last_name": "Apellido",
    "email": "Email",
    "is_superuser": "Es superusuario",
    "is_staff": "Es staff",
    "is_active": "Activo",
    "date_joined": "Fecha de creación",
    "dia": "Día",
    "accion": "Acción",
    "ultima_ejecucion": "Última ejecución",
    "ultimos_4": "Últimos 4 dígitos",
    "acepta_credito": "Acepta crédito",
    "acepta_debito": "Acepta débito",
    "acepta_link": "Acepta link de pago",
    "terminal_id": "Terminal",
    "cuenta_id": "Cuenta",
    "proveedor_id": "Proveedor",
}


def _valor_foto(f, obj):
    valor = f.value_from_object(obj)
    return (valor.name or "") if isinstance(f, models.FileField) else valor


def foto(obj):
    return {
        f.attname: _valor_foto(f, obj)
        for f in obj._meta.concrete_fields
        if not f.primary_key and f.attname not in EXCLUIDOS
    }


def registrar(
    tipo,
    *,
    usuario,
    entidad=None,
    cambios=(),
    origen="",
    detalle="",
    exito=None,
    usuario_intentado="",
):
    ip, dispositivo = contexto_actual()
    return Evento.objects.create(
        tipo=tipo,
        fecha=timezone.now(),
        usuario=usuario,
        origen=origen,
        usuario_intentado=usuario_intentado,
        entidad=entidad._meta.label_lower if entidad is not None else "",
        entidad_id=entidad.pk if entidad is not None else None,
        ip=ip,
        dispositivo=dispositivo,
        cambios=list(cambios),
        exito=exito,
        detalle=detalle,
    )


def registrar_alta(obj, *, usuario, origen="", extra=None):
    datos = foto(obj) | (extra or {})
    cambios = [{"campo": k, "anterior": None, "nuevo": datos[k]} for k in sorted(datos)]
    return registrar(Evento.Tipo.ALTA, usuario=usuario, entidad=obj, cambios=cambios, origen=origen)


def registrar_cambio(obj, antes, *, usuario, origen="", despues=None, detalle=""):
    despues = foto(obj) if despues is None else despues
    cambios = [
        {"campo": k, "anterior": antes.get(k), "nuevo": despues.get(k)}
        for k in sorted(antes | despues)
        if antes.get(k) != despues.get(k)
    ]
    if not cambios and not detalle:
        return None
    return registrar(
        Evento.Tipo.CAMBIO,
        usuario=usuario,
        entidad=obj,
        cambios=cambios,
        origen=origen,
        detalle=detalle,
    )


def registrar_borrado(obj, *, usuario, origen=""):
    datos = foto(obj)
    cambios = [{"campo": k, "anterior": datos[k], "nuevo": None} for k in sorted(datos)]
    return registrar(
        Evento.Tipo.BORRADO, usuario=usuario, entidad=obj, cambios=cambios, origen=origen
    )


def historial_de(obj):
    return Evento.objects.filter(entidad=obj._meta.label_lower, entidad_id=obj.pk)


def quien(evento):
    if evento.tipo == Evento.Tipo.LOGIN_FALLIDO:
        return evento.usuario_intentado
    if evento.usuario_id:
        return evento.usuario.username
    return f"Sistema — {evento.origen}" if evento.origen else "Sistema"


def _modelo(entidad):
    try:
        return apps.get_model(entidad)
    except (LookupError, ValueError):
        return None


def _campo(modelo, attname):
    if modelo is None:
        return None
    return next((f for f in modelo._meta.concrete_fields if f.attname == attname), None)


def rotulo(campo, attname):
    if attname in ROTULOS:
        return ROTULOS[attname]
    if campo is None:
        return attname.replace("_", " ")  # campo que no es del modelo: texto libre
    return capfirst(str(campo.verbose_name))


def _vacio(valor):
    return valor is None or valor == ""


def _nombre(obj):
    if isinstance(obj, get_user_model()):
        return obj.get_full_name() or obj.get_username()
    return str(obj)


def _valor(campo, valor, relaciones=None):
    if _vacio(valor):
        return "—"
    if campo is not None:
        tipo = campo.get_internal_type()
        if campo.is_relation:
            obj = (relaciones or {}).get(campo.related_model, {}).get(valor)
            return _nombre(obj) if obj is not None else f"N.º {valor}"
        if tipo == "FileField":
            return "Archivo adjunto"
        if tipo == "DecimalField":
            return pesos(Decimal(valor))
        if campo.flatchoices:
            return str(dict(campo.flatchoices).get(valor, valor))
        if tipo in ("DateTimeField", "DateField") and isinstance(valor, str):
            if tipo == "DateField":
                fecha = parse_date(valor)
                return formato_fecha(fecha, "d/m/Y") if fecha else valor
            fecha = parse_datetime(valor)
            return formato_fecha(localtime(fecha), "d/m/Y H:i") if fecha else valor
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    return str(valor)


def _relaciones(eventos):
    """Un `in_bulk` por modelo relacionado para toda la página (020/FR-021)."""
    pks = defaultdict(set)
    for e in eventos:
        modelo = _modelo(e.entidad)
        for c in e.cambios:
            campo = _campo(modelo, c["campo"])
            if campo is not None and campo.is_relation:
                pks[campo.related_model] |= {
                    v for v in (c["anterior"], c["nuevo"]) if not _vacio(v)
                }
    resultado = {}
    for rel, ids in pks.items():
        fks = [f.name for f in rel._meta.concrete_fields if f.many_to_one]
        resultado[rel] = rel._default_manager.select_related(*fks).in_bulk(ids)
    return resultado


def _visible(tipo, c):
    if tipo == Evento.Tipo.ALTA:
        return not _vacio(c["nuevo"])
    if tipo == Evento.Tipo.BORRADO:
        return not _vacio(c["anterior"])
    return c["anterior"] != c["nuevo"] and not (_vacio(c["anterior"]) and _vacio(c["nuevo"]))


def lineas(evento, relaciones=None):
    modelo = _modelo(evento.entidad)
    cambios = [c for c in evento.cambios if _visible(evento.tipo, c)]
    if relaciones is None:
        relaciones = _relaciones([evento])
    resultado = []
    for c in cambios:
        campo = _campo(modelo, c["campo"])
        resultado.append(
            (
                rotulo(campo, c["campo"]),
                _valor(campo, c["anterior"], relaciones),
                _valor(campo, c["nuevo"], relaciones),
            )
        )
    return resultado


def lineas_de_pagina(eventos):
    relaciones = _relaciones(eventos)
    for e in eventos:
        e.lineas = lineas(e, relaciones)


_REINICIOS = (Evento.Tipo.INICIO_SESION, Evento.Tipo.BLOQUEO, Evento.Tipo.DESBLOQUEO)


def _ultimo_reinicio(user):
    return historial_de(user).filter(tipo__in=_REINICIOS).first()


def fallos_seguidos(user):
    fallos = Evento.objects.filter(
        tipo=Evento.Tipo.LOGIN_FALLIDO, usuario_intentado=user.username
    ).exclude(detalle=BLOQUEADO)
    reinicio = _ultimo_reinicio(user)
    if reinicio:
        fallos = fallos.filter(fecha__gt=reinicio.fecha)
    return fallos.count()


def esta_bloqueado(user, ahora=None):
    ultimo = _ultimo_reinicio(user)
    if ultimo is None or ultimo.tipo != Evento.Tipo.BLOQUEO:
        return False
    return (ahora or timezone.now()) < ultimo.fecha + DURACION_BLOQUEO


def desbloquear_usuario(objetivo, *, usuario):
    if not esta_bloqueado(objetivo):
        raise ValidationError("El usuario no está bloqueado.")
    return registrar(Evento.Tipo.DESBLOQUEO, usuario=usuario, entidad=objetivo)
