"""Servicios de 007: comprobante, saldo disponible y validación de ítems."""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from sistema.arca import servicios as arca
from sistema.asignacion.servicios import (
    avisar_cruces,
    crear_cobro_pendiente,
    es_excedente,
    fila,
    medir,
    usado_mes,
)
from sistema.auditoria.servicios import foto, registrar_alta, registrar_cambio
from sistema.avisos import servicios as avisos
from sistema.cobros import comprobante as comprobantes
from sistema.cobros.models import (
    METODOS_POS,
    METODOS_TARJETA,
    Cobro,
    ComprobanteCobro,
    ItemCobro,
    PagoComplementario,
)
from sistema.core.models import MontoField
from sistema.core.templatetags.pesos import pesos
from sistema.dentalink import cliente
from sistema.dentalink import servicios as dentalink
from sistema.core.tiempo import ARG, inicio_dia_arg, rango_mes_arg
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.terminales.models import Terminal
from sistema.terminales.servicios import acumulado, avisar_objetivo, cuenta_para_cobro
from sistema.usuarios import roles

CERO = Decimal("0.00")
ESTADOS_RESERVAN = ("pendiente", "error", "revisar")

logger = logging.getLogger(__name__)


def _reservan():
    return ItemCobro.objects.filter(estado_envio__in=ESTADOS_RESERVAN).exclude(
        cobro__estado="cancelado"
    )


def reservado(id_prestacion, *, excluir_item=None) -> Decimal:
    """Suma de lo que ya reservan otros ítems sobre la prestación (research R3)."""
    return (
        _reservan()
        .filter(id_prestacion=id_prestacion)
        .exclude(pk=excluir_item)
        .aggregate(total=Coalesce(Sum("monto"), CERO, output_field=MontoField()))["total"]
    )


def cuenta_efectivo():
    cuenta = Cuenta.objects.habilitadas().filter(tipo="efectivo").first()
    if cuenta is None:
        raise ValidationError("No hay una cuenta de efectivo Activa. Avisá a un administrador.")
    return cuenta


def prestaciones_para_cobrar(id_paciente, *, cobro=None) -> list[dict]:
    """Tratamientos de `prestaciones_de` con `saldo_disponible` por prestación (R3).

    Lo reservado se lee ANTES de Dentalink: el orden inverso deja cobrar de más.
    """
    reservado_por = {
        r["id_prestacion"]: r["total"]
        for r in _reservan().values("id_prestacion").annotate(total=Sum("monto"))
    }
    propios = {}
    if cobro is not None:
        propios = {i.id_prestacion: i for i in cobro.items.all()}
    resultado = []
    for t in dentalink.prestaciones_de(id_paciente):
        prestaciones = []
        for p in t["prestaciones"]:
            res = reservado_por.get(p["id"], CERO)
            item = propios.get(p["id"])
            if item is not None:
                if item.estado_envio in ESTADOS_RESERVAN:
                    res -= item.monto
                disponible = p["saldo"] - res
                if item.estado_envio == ItemCobro.EstadoEnvio.ENVIADO:
                    disponible += item.monto
            else:
                disponible = p["saldo"] - res
            if disponible > CERO or p["saldo_a_favor"]:
                prestaciones.append({**p, "saldo_disponible": disponible})
        if prestaciones:
            resultado.append({**t, "prestaciones": prestaciones})
    return resultado


def _validar_items(items, prestaciones) -> list[ItemCobro]:
    """Valida los ítems contra `prestaciones_para_cobrar` y arma ItemCobro sin guardar."""
    if not items:
        raise ValidationError("Elegí al menos una prestación.")
    por_id = {}
    for t in prestaciones:
        for p in t["prestaciones"]:
            por_id[p["id"]] = (t, p)
    a_favor = next((par for par in por_id.values() if par[1]["saldo_a_favor"]), None)
    vistos = set()
    con_saldo_a_favor = 0
    resultado = []
    for numero, datos in enumerate(items, start=1):
        marca = bool(datos.get("saldo_a_favor"))
        if marca:
            if a_favor is None:
                raise ValidationError(
                    "El paciente no tiene la prestación «saldo a favor» en Dentalink."
                )
            con_saldo_a_favor += 1
            if con_saldo_a_favor > 1:
                raise ValidationError("Solo se admite un ítem de saldo a favor.")
            tratamiento, prestacion = a_favor
        else:
            if datos["id_prestacion"] not in por_id:
                raise ValidationError("La prestación no tiene saldo para cobrar.")
            tratamiento, prestacion = por_id[datos["id_prestacion"]]
        if prestacion["id"] in vistos:
            raise ValidationError("Elegiste 2 veces la misma prestación.")
        vistos.add(prestacion["id"])
        monto = datos["monto"]
        if monto <= CERO:
            raise ValidationError("El monto del ítem debe ser mayor que $0,00.")
        if not marca and monto > prestacion["saldo_disponible"]:
            raise ValidationError(
                f"El ítem de «{prestacion['nombre']}» supera el saldo disponible de "
                f"{pesos(prestacion['saldo_disponible'])}."
            )
        propuesto = prestacion["profesional_propuesto"]
        if marca or datos.get("id_profesional") is None:
            profesional = propuesto
        else:
            profesional = next(
                (x for x in prestacion["profesionales"] if x["id"] == datos["id_profesional"]),
                None,
            )
            if profesional is None:
                raise ValidationError("El profesional no está en ese tratamiento.")
        resultado.append(
            ItemCobro(
                numero=numero,
                id_tratamiento=tratamiento["id"],
                id_prestacion=prestacion["id"],
                id_profesional=profesional["id"],
                nombre_tratamiento=tratamiento["nombre"],
                nombre_prestacion=prestacion["nombre"],
                nombre_profesional=profesional["nombre"],
                monto=monto,
                saldo_a_favor=marca,
            )
        )
    return resultado


def _con_archivo(cobro, archivo):
    """Pone el comprobante validado y comprimido (FR-034). Sin archivo, no toca nada."""
    if archivo:
        cobro.comprobante = comprobantes.validar_y_comprimir(archivo)


def _traducir(error):
    """Un IntegrityError de constraint pasa a ValidationError con su mensaje en español."""
    for c in (*Cobro._meta.constraints, *ItemCobro._meta.constraints):
        if c.name in str(error) and c.violation_error_message:
            return ValidationError(c.violation_error_message)
    return None


def _cuenta_pos(terminal, metodo, ahora):
    """Terminal Activa que acepta el método -> cuenta de su sesión en `ahora` (FR-003, FR-028)."""
    if terminal is None:
        raise ValidationError("Elegí la terminal POS.")
    if terminal.estado != Terminal.Estado.ACTIVA:
        raise ValidationError("La terminal no está Activa.")
    if not terminal.acepta(metodo):
        raise ValidationError(f"La terminal no acepta {Cobro.Metodo(metodo).label}.")
    return cuenta_para_cobro(terminal, ahora)


def crear_cobro(
    *,
    usuario,
    metodo,
    monto,
    id_paciente=None,
    nombre_paciente="",
    items=(),
    terminal=None,
    monto_neto=None,
    numero_operacion=None,
    banco_emisor=None,
    ultimos_4=None,
    comprobante=None,
    observaciones="",
) -> Cobro:
    """Alta de un cobro con sus ítems (contrato, "Alta"). Sin `id_paciente`: «Sin vincular a Dentalink»."""
    cobro = Cobro()
    antes = cuenta = None
    try:
        with transaction.atomic():
            fila()
            ahora = timezone.now()
            if id_paciente is None:  # «Sin vincular a Dentalink»: no llama a Dentalink (FR-036)
                if not nombre_paciente.strip():
                    raise ValidationError("Escribí el nombre del paciente.")
                if items:
                    raise ValidationError("Un cobro «Sin vincular a Dentalink» no lleva ítems.")
                if monto <= CERO:
                    raise ValidationError("El monto debe ser mayor que $0,00.")
                validados = []
                datos_paciente = {
                    "nombre_paciente": nombre_paciente.strip(),
                    "faltan_items": True,
                    "observaciones": observaciones,
                }
            else:
                datos = cliente.paciente(id_paciente)
                validados = _validar_items(items, prestaciones_para_cobrar(id_paciente))
                if monto <= CERO:
                    raise ValidationError("El monto debe ser mayor que $0,00.")
                if monto != sum((i.monto for i in validados), CERO):
                    raise ValidationError("El monto del cobro no es igual a la suma de los ítems.")
                datos_paciente = {
                    "id_paciente_dentalink": datos["id"],
                    "nombre_paciente": datos["nombre"],
                    "dni_paciente": datos["dni"],
                    "observaciones": observaciones,
                }
            if metodo in METODOS_POS:
                cuenta = _cuenta_pos(terminal, metodo, ahora)
                antes = acumulado(cuenta)
                if banco_emisor is not None and not banco_emisor.vigente:
                    raise ValidationError("Elegí un banco vigente de la copia de Dentalink.")
                if not comprobante:
                    raise ValidationError("Falta el comprobante.")
                cobro = Cobro(
                    cuenta=cuenta,
                    destinatario=cuenta.destinatario,
                    terminal=terminal,
                    metodo=metodo,
                    monto=monto,
                    monto_neto=monto_neto,
                    numero_operacion=numero_operacion,
                    banco_emisor=banco_emisor,
                    ultimos_4=ultimos_4,
                    estado=Cobro.Estado.FINALIZADO,
                    fecha=ahora,
                    creado_por=usuario,
                    **datos_paciente,
                )
                _con_archivo(cobro, comprobante)
                cobro.full_clean()
                cobro.save()
                registrar_alta(cobro, usuario=usuario)
            elif metodo == Cobro.Metodo.TRANSFERENCIA:
                # nace Pendiente y sin comprobante; el comprobante llega con adjuntar_comprobante
                cobro = crear_cobro_pendiente(
                    monto=monto, usuario=usuario, metodo=metodo, **datos_paciente
                )
            else:
                caja = cuenta_efectivo()  # el comprobante de efectivo se ignora (FR-023)
                cobro = Cobro(
                    cuenta=caja,
                    destinatario=caja.destinatario,
                    metodo=metodo,
                    monto=monto,
                    estado=Cobro.Estado.FINALIZADO,
                    fecha=ahora,
                    creado_por=usuario,
                    **datos_paciente,
                )
                cobro.full_clean()
                cobro.save()
                registrar_alta(cobro, usuario=usuario)
            for item in validados:
                item.cobro = cobro
                item.full_clean()
                item.save()
                registrar_alta(item, usuario=usuario)
            if cuenta is not None:
                avisar_objetivo(cuenta, antes)
            dentalink.encolar_envio(cobro)
            arca.al_finalizar(cobro)
    except IntegrityError as e:
        _borrar_archivo(cobro)
        raise (_traducir(e) or e) from e
    except Exception:
        _borrar_archivo(cobro)
        raise
    return cobro


def _borrar_archivo(obj):
    """Si el alta falla después de escribir el comprobante, no deja el archivo huérfano (FR-049)."""
    campo = obj.archivo if isinstance(obj, ComprobanteCobro) else obj.comprobante
    if campo and campo.name:
        campo.storage.delete(campo.name)


def _comprobar_visto(cobro, estado_visto, *, adjuntando=False):
    """FR-020: la pantalla mostraba otro estado que el de la base. Textos de 021/FR-007."""
    recargar = "Recargá la página para ver el estado actual."
    if not estado_visto:
        raise ValidationError(recargar)
    if cobro.estado == estado_visto:
        return
    if adjuntando and cobro.estado == Cobro.Estado.CANCELADO:
        raise ValidationError(
            "Un administrador canceló este cobro. No transfieras. "
            "Si ya transferiste, avisá a un administrador."
        )
    if adjuntando and cobro.estado == Cobro.Estado.FINALIZADO:
        raise ValidationError("El cobro ya está Finalizado. No hace falta otro comprobante.")
    raise ValidationError(f"El cobro cambió a {cobro.get_estado_display()}. {recargar}")


def adjuntar_comprobante(
    cobro, archivo, *, usuario, estado_visto=None, observaciones=None
) -> Cobro:
    """Agrega un comprobante (FR-052). Pendiente -> Finalizado; el administrador suma más
    a un Finalizado por transferencia sin cambiar nada más. No mira la cuenta (FR-047)."""
    nuevo = None
    try:
        with transaction.atomic():
            cobro = Cobro.objects.select_for_update().get(pk=cobro.pk)
            if usuario.role == roles.ADMIN or estado_visto is not None:
                _comprobar_visto(cobro, estado_visto, adjuntando=True)
            if cobro.estado == Cobro.Estado.CANCELADO:
                raise ValidationError("El cobro ya está Cancelado.")
            finalizado = cobro.estado == Cobro.Estado.FINALIZADO
            if finalizado:
                if cobro.metodo != Cobro.Metodo.TRANSFERENCIA:
                    raise ValidationError(
                        "Solo un cobro por transferencia admite más comprobantes."
                    )
                if usuario.role != roles.ADMIN:
                    raise ValidationError("El cobro ya está Finalizado.")
            if not archivo:
                raise ValidationError("Falta el comprobante.")
            antes = foto(cobro)
            if observaciones is not None:
                cobro.observaciones = observaciones.strip()
            if cobro.comprobante:
                # El 1.er comprobante vive en `Cobro.comprobante`; del 2.º en adelante, aparte.
                extra = ComprobanteCobro(
                    cobro=cobro,
                    archivo=comprobantes.validar_y_comprimir(archivo),
                    creado_por=usuario,
                )
                nuevo = extra
                extra.full_clean()
                extra.save()
                registrar_alta(extra, usuario=usuario)
            else:
                _con_archivo(cobro, archivo)
                nuevo = cobro
            if not finalizado:
                cobro.estado = Cobro.Estado.FINALIZADO
                cobro.full_clean()
                cobro.save()
            elif observaciones is not None:
                cobro.save(update_fields=["observaciones"])
            registrar_cambio(
                cobro,
                antes,
                usuario=usuario,
                detalle="Comprobante agregado." if finalizado or nuevo is not cobro else "",
            )
            if not finalizado:
                dentalink.encolar_envio(cobro)
                # Un cobro reabierto no se factura de nuevo, ni tras una nota de crédito.
                if not cobro.comprobantes_fiscales.exists():
                    arca.al_finalizar(cobro)
    except Exception:
        if nuevo is not None:
            _borrar_archivo(nuevo)
        raise
    return cobro


def reabrir_cobro(cobro, *, usuario, motivo, estado_visto) -> Cobro:
    """Finalizado -> Pendiente por transferencia (FR-054). Cuenta, monto y cupo no cambian."""
    if usuario.role != roles.ADMIN:
        raise PermissionDenied
    if not (motivo or "").strip():
        raise ValidationError("Escribí el motivo de la reapertura.")
    with transaction.atomic():
        cobro = Cobro.objects.select_for_update().get(pk=cobro.pk)
        _comprobar_visto(cobro, estado_visto)
        if cobro.metodo != Cobro.Metodo.TRANSFERENCIA:
            raise ValidationError("Solo un cobro por transferencia se puede reabrir.")
        if cobro.estado != Cobro.Estado.FINALIZADO:
            raise ValidationError(f"El cobro ya está {cobro.get_estado_display()}.")
        antes = foto(cobro)
        cobro.estado = Cobro.Estado.PENDIENTE
        cobro.full_clean()
        cobro.save()
        dentalink.al_cambiar_cobro(cobro, usuario=usuario, cancelado=False)
        registrar_cambio(cobro, antes, usuario=usuario, detalle=motivo.strip())
    return cobro


def completar_items(cobro, *, usuario, id_paciente, items) -> Cobro:
    """Cobro «Sin vincular a Dentalink»: pone paciente e ítems de Dentalink y quita la marca (FR-038)."""
    with transaction.atomic():
        fila()
        cobro = Cobro.objects.select_for_update().get(pk=cobro.pk)
        if not cobro.faltan_items or cobro.estado == Cobro.Estado.CANCELADO:
            raise ValidationError("El cobro no está «Sin vincular a Dentalink».")
        datos = cliente.paciente(id_paciente)
        validados = _validar_items(items, prestaciones_para_cobrar(id_paciente))
        suma = sum((i.monto for i in validados), CERO)
        if suma != cobro.monto:
            raise ValidationError(
                f"Los ítems suman {pesos(suma)} y el cobro es de {pesos(cobro.monto)}."
            )
        antes = foto(cobro)
        cobro.id_paciente_dentalink = datos["id"]
        cobro.nombre_paciente = datos["nombre"]
        cobro.dni_paciente = datos["dni"]
        cobro.faltan_items = False
        cobro.full_clean()
        cobro.save()
        for item in validados:
            item.cobro = cobro
            item.full_clean()
            item.save()
            registrar_alta(item, usuario=usuario)
        registrar_cambio(cobro, antes, usuario=usuario)
        dentalink.encolar_envio(cobro)
        arca.al_finalizar(cobro)
    return cobro


def cancelar_cobro(cobro, *, usuario, motivo, detalle="", estado_visto, nota_credito=None) -> Cobro:
    with transaction.atomic():
        cobro = Cobro.objects.select_for_update().get(pk=cobro.pk)
        _comprobar_visto(cobro, estado_visto)
        if cobro.estado == Cobro.Estado.CANCELADO:
            raise ValidationError("El cobro ya está Cancelado.")
        if not (motivo or "").strip():
            raise ValidationError("Elegí un motivo de cancelación.")
        if usuario.role != roles.ADMIN and (
            cobro.estado == Cobro.Estado.FINALIZADO or arca.factura_emitida(cobro)
        ):
            # Con factura emitida (también en un cobro reabierto) cancela solo el administrador.
            raise PermissionDenied
        antes = foto(cobro)
        antes_acumulado = acumulado(cobro.cuenta) if cobro.es_pos else None
        cobro.estado = Cobro.Estado.CANCELADO
        cobro.motivo_cancelacion = motivo
        cobro.detalle_cancelacion = detalle
        cobro.excedente = es_excedente(cobro)
        cobro.full_clean()
        cobro.save()
        arca.al_cambiar_cobro(
            cobro, antes, usuario=usuario, cancelado=True, nota_credito=nota_credito
        )
        dentalink.al_cambiar_cobro(cobro, usuario=usuario, cancelado=True)
        if cobro.es_pos:
            avisar_objetivo(cobro.cuenta, antes_acumulado)
        registrar_cambio(cobro, antes, usuario=usuario)
    return cobro


CAMPOS_EDITABLES = (
    "fecha",
    "monto",
    "metodo",
    "cuenta",
    "terminal",
    "monto_neto",
    "numero_operacion",
    "banco_emisor",
    "ultimos_4",
    "observaciones",
)
CAMPOS_POS = ("terminal", "monto_neto", "numero_operacion", "banco_emisor", "ultimos_4")


def _cuenta_editada(cobro, datos, metodo_viejo):
    """Cuenta del cobro editado según el método nuevo (FR-040, FR-042). `cobro` ya tiene `datos`."""
    metodo = cobro.metodo
    cambio_metodo = metodo != metodo_viejo
    if metodo == Cobro.Metodo.TRANSFERENCIA:
        cuenta = datos.get("cuenta", cobro.cuenta)
        if ("cuenta" not in datos and cambio_metodo) or cuenta.tipo != Cuenta.Tipo.TRANSFERENCIA:
            raise ValidationError("Elegí una cuenta de transferencia.")
        return cuenta
    if "cuenta" in datos:
        raise ValidationError("La cuenta solo se elige en una transferencia.")
    if metodo == Cobro.Metodo.EFECTIVO:
        return cuenta_efectivo() if cambio_metodo else cobro.cuenta
    if cambio_metodo or "terminal" in datos:
        return _cuenta_pos(cobro.terminal, metodo, cobro.fecha)
    if "fecha" in datos:
        return cuenta_para_cobro(cobro.terminal, cobro.fecha)
    return cobro.cuenta


def _editar_items(cobro, items, usuario):
    """Cambia monto y profesional de ítems existentes y agrega nuevos. Devuelve si cambió algo."""
    existentes = {i.pk: i for i in cobro.items.select_for_update()}
    pedidos = []
    for datos in items:
        if datos.get("id") is None:
            pedidos.append((None, {"saldo_a_favor": False, **datos}))
            continue
        item = existentes.get(datos["id"])
        if item is None:
            raise ValidationError("El ítem no pertenece al cobro.")
        monto = datos.get("monto", item.monto)
        profesional = datos.get("id_profesional", item.id_profesional)
        if (monto, profesional) == (item.monto, item.id_profesional):
            continue
        pedidos.append(
            (
                item,
                {
                    "id_prestacion": item.id_prestacion,
                    "monto": monto,
                    "id_profesional": profesional,
                    "saldo_a_favor": item.saldo_a_favor,
                },
            )
        )
    if not pedidos:
        return False
    prestaciones = prestaciones_para_cobrar(cobro.id_paciente_dentalink, cobro=cobro)
    validados = _validar_items([d for _, d in pedidos], prestaciones)
    siguiente = max((i.numero for i in existentes.values()), default=0)
    for (item, _), valido in zip(pedidos, validados, strict=True):
        if item is None:
            siguiente += 1
            valido.numero = siguiente
            valido.cobro = cobro
            valido.full_clean()
            valido.save()
            registrar_alta(valido, usuario=usuario)
            continue
        antes = foto(item)
        item.monto = valido.monto
        item.id_profesional = valido.id_profesional
        item.nombre_profesional = valido.nombre_profesional
        item.full_clean()
        item.save()
        registrar_cambio(item, antes, usuario=usuario)
    return True


def editar_cobro(
    cobro, *, usuario, estado_visto, items=None, comprobante=None, nota_credito=None, **datos
) -> Cobro:
    """Correcciones del administrador (contrato, "Edición"). No quita ítems ni cambia paciente."""
    if usuario.role != roles.ADMIN:
        raise PermissionDenied
    for clave in datos:
        if clave == "id_paciente_dentalink":
            raise ValidationError("El paciente no cambia. Cancelá el cobro y creá otro.")
        if clave not in CAMPOS_EDITABLES:
            raise ValidationError(f"El campo {clave} no se edita.")
    archivo = None
    try:
        with transaction.atomic():
            fila()
            terminales = {datos["terminal"].pk} if datos.get("terminal") else set()
            terminales |= set(
                Cobro.objects.filter(pk=cobro.pk, terminal__isnull=False).values_list(
                    "terminal_id", flat=True
                )
            )
            list(Terminal.objects.select_for_update().filter(pk__in=terminales).order_by("pk"))
            cobro = Cobro.objects.select_for_update().get(pk=cobro.pk)
            if cobro.estado != estado_visto:
                raise ValidationError(
                    f"El cobro cambió a {cobro.get_estado_display()}. "
                    "Revisá los datos y volvé a intentar."
                )
            if cobro.estado == Cobro.Estado.CANCELADO and (
                set(datos) - {"observaciones"} or items or comprobante
            ):
                raise ValidationError("Un cobro Cancelado solo acepta cambios en observaciones.")
            if (
                cobro.estado == Cobro.Estado.PENDIENTE
                and datos.get("metodo", cobro.metodo) != cobro.metodo
            ):
                raise ValidationError(
                    "Un cobro Pendiente no cambia de método de cobro. "
                    "Cancelalo con «Se cobró por otro medio» y creá otro."
                )
            antes = foto(cobro)
            cuenta_vieja, metodo_viejo, fecha_vieja = cobro.cuenta, cobro.metodo, cobro.fecha
            banco = datos.get("banco_emisor")
            if banco is not None and not banco.vigente:
                raise ValidationError("Elegí un banco vigente de la copia de Dentalink.")
            for clave, valor in datos.items():
                if clave != "cuenta":
                    setattr(cobro, clave, valor)
            if cobro.metodo != metodo_viejo:
                if cobro.metodo not in METODOS_POS:
                    for clave in CAMPOS_POS:
                        setattr(cobro, clave, None)
                elif cobro.metodo not in METODOS_TARJETA:
                    cobro.banco_emisor = cobro.ultimos_4 = None
            if cobro.estado == Cobro.Estado.CANCELADO:
                cuenta = cuenta_vieja
            else:
                cuenta = _cuenta_editada(cobro, datos, metodo_viejo)
            cobro.cuenta, cobro.destinatario = cuenta, cuenta.destinatario
            medidos = medir([(cuenta_vieja, fecha_vieja), (cuenta, cobro.fecha)])
            paso_a_excedente = False
            acumulados = {
                c: acumulado(c)
                for c in {cuenta_vieja, cuenta}
                if c.tipo == Cuenta.Tipo.TERMINAL_POS
            }
            if comprobante and cobro.metodo != Cobro.Metodo.EFECTIVO:
                _con_archivo(cobro, comprobante)
                archivo = cobro
            item_cambiado = False
            if items:
                if cobro.estado == Cobro.Estado.CANCELADO or cobro.faltan_items:
                    raise ValidationError("El cobro no admite cambios en los ítems.")
                item_cambiado = _editar_items(cobro, items, usuario)
            if not cobro.faltan_items:
                suma = cobro.items.aggregate(t=Sum("monto"))["t"]
                if suma is not None and suma != cobro.monto:
                    raise ValidationError("El monto del cobro no es igual a la suma de los ítems.")
            if cobro.estado != Cobro.Estado.CANCELADO and (
                {"monto", "fecha", "metodo"} & set(datos) or cuenta != cuenta_vieja
            ):
                era = cobro.excedente
                cobro.excedente = es_excedente(cobro)
                if cobro.excedente and not era:
                    cobro.revisado = False
                    paso_a_excedente = True
            cobro.full_clean()
            cobro.save()
            registrar_cambio(cobro, antes, usuario=usuario)
            arca.al_cambiar_cobro(cobro, antes, usuario=usuario, nota_credito=nota_credito)
            avisar_cruces(medidos, cobro)
            if paso_a_excedente:
                avisos.excedente(cobro=cobro, usado=usado_mes(cobro.cuenta, cobro.fecha))
            if (
                item_cambiado
                or {"fecha", "metodo", "banco_emisor"} & set(datos)
                or cuenta != cuenta_vieja
            ):
                dentalink.al_cambiar_cobro(cobro, usuario=usuario, cancelado=False)
            for c, antes_acumulado in acumulados.items():
                avisar_objetivo(c, antes_acumulado)
            dentalink.encolar_envio(cobro)
            if cobro.destinatario_id != antes["destinatario_id"]:
                # 014/FR-008: otra edición no factura un cobro viejo ni vuelve a facturar
                # después de una nota de crédito pedida sola (FR-018).
                arca.al_finalizar(cobro)
    except Exception as e:
        if archivo is not None:
            _borrar_archivo(archivo)
        if isinstance(e, IntegrityError) and (traducido := _traducir(e)):
            raise traducido from e
        raise
    return cobro


def cambiar_observaciones(cobro, *, usuario, observaciones) -> Cobro:
    """018/FR-015: cambia las observaciones. El administrador, en todo estado; el operador, solo en
    sus cobros Pendiente o Finalizado. Sin cambios, sin evento. No toca cupo, factura ni Dentalink."""
    with transaction.atomic():
        cobro = Cobro.objects.select_for_update().get(pk=cobro.pk)
        if usuario.role == roles.OPERATOR and cobro.creado_por_id == usuario.pk:
            if cobro.estado == Cobro.Estado.CANCELADO:
                raise ValidationError("El cobro ya está Cancelado.")
        elif usuario.role != roles.ADMIN:
            raise PermissionDenied
        nuevo = (observaciones or "").strip()
        if nuevo == cobro.observaciones:
            return cobro
        antes = foto(cobro)
        cobro.observaciones = nuevo
        cobro.full_clean()
        cobro.save()
        registrar_cambio(cobro, antes, usuario=usuario)
    return cobro


def marcar_revisado(cobro, *, usuario, revisado=True, comentario="") -> Cobro:
    """FR-014, FR-015: el administrador marca o quita la revisión. El comentario es obligatorio
    solo al marcar un cobro con excedente. Sin cambios, sin evento."""
    if usuario.role != roles.ADMIN:
        raise PermissionDenied
    comentario = (comentario or "").strip()
    with transaction.atomic():
        cobro = Cobro.objects.select_for_update().get(pk=cobro.pk)
        if revisado and cobro.excedente and not comentario:
            raise ValidationError("Escribí un comentario.")
        antes = foto(cobro)
        cobro.revisado = revisado
        cobro.comentario_revision = comentario if revisado else ""
        cobro.full_clean()
        cobro.save()
        registrar_cambio(cobro, antes, usuario=usuario)
    return cobro


def marcar_revisados(pks, *, usuario) -> tuple[list[int], list[int]]:
    """021/FR-019: marca revisados los cobros elegidos, en una transacción por cobro (research R7).
    Un excedente sin comentario no se marca. Devuelve (marcados, sin_comentario)."""
    if usuario.role != roles.ADMIN:
        raise PermissionDenied
    marcados, sin_comentario = [], []
    for cobro in Cobro.objects.filter(pk__in=pks, revisado=False).order_by("pk"):
        if cobro.excedente and not cobro.comentario_revision:
            sin_comentario.append(cobro.pk)
            continue
        try:
            with transaction.atomic():
                marcar_revisado(cobro, usuario=usuario, comentario=cobro.comentario_revision)
        except ValidationError:
            continue
        marcados.append(cobro.pk)
    return marcados, sin_comentario


def cancelar_cobros(pks, *, usuario, motivo, detalle="") -> tuple[list[int], list[int]]:
    """FR-017: cancela en lote los cobros Pendiente. Una transacción por cobro (research R5):
    el que falla queda omitido y los demás siguen. Devuelve (cancelados, omitidos)."""
    if usuario.role != roles.ADMIN:
        raise PermissionDenied
    if motivo not in Cobro.MotivoCancelacion.values:
        raise ValidationError("Elegí un motivo de cancelación.")
    detalle = (detalle or "").strip()
    if motivo == Cobro.MotivoCancelacion.OTRO and not detalle:
        raise ValidationError("Con el motivo «Otro», escribí el detalle.")
    cancelados, omitidos = [], []
    for cobro in Cobro.objects.filter(pk__in=pks).order_by("pk"):
        if cobro.estado != Cobro.Estado.PENDIENTE:
            omitidos.append(cobro.pk)
            continue
        try:
            with transaction.atomic():
                cancelar_cobro(
                    cobro, usuario=usuario, motivo=motivo, detalle=detalle, estado_visto="pendiente"
                )
        except ValidationError:
            omitidos.append(cobro.pk)
        except Exception:
            logger.exception("Error inesperado al cancelar cobro en lote: cobro=%s", cobro.pk)
            omitidos.append(cobro.pk)  # motivo: "Error inesperado"
        else:
            cancelados.append(cobro.pk)
    return cancelados, omitidos


def mes_propuesto(ahora=None) -> date:
    """Día 1 a 10: mes anterior. Día 11 o más: mes corriente (FR-009)."""
    local = timezone.localtime(ahora or timezone.now(), ARG)
    primero = local.date().replace(day=1)
    if local.day >= 11:
        return primero
    return (primero.replace(day=1) - timedelta(days=1)).replace(day=1)


def _totales(queryset, campo) -> dict:
    filas = queryset.values("destinatario").annotate(total=Sum(campo))
    return {f["destinatario"]: f["total"] for f in filas}


def resumen_mes(mes, *, con_falta=False, destinatarios=None) -> list[Destinatario]:
    """Esperado, recibido y falta de cada destinatario visible en `mes` (FR-001 a FR-004)."""
    desde, hasta = rango_mes_arg(inicio_dia_arg(mes))
    cobros = _totales(
        Cobro.objects.filter(
            estado="finalizado", fecha__gte=desde, fecha__lt=hasta, destinatario__isnull=False
        ),
        "monto",
    )
    pagos = _totales(PagoComplementario.objects.filter(estado="registrado", mes=mes), "monto")
    activas = Cuenta.objects.filter(estado="activa")
    from sistema.destinatarios.servicios import esperado_mensual  # destinatarios importa cobros

    topes = {}
    for c in activas.filter(tipo="transferencia").prefetch_related(
        "topes"
    ):  # esperado del mes pedido (021/FR-027)
        topes[c.destinatario_id] = topes.get(c.destinatario_id, CERO) + esperado_mensual(c, desde)
    objetivos = {
        f["destinatario"]: f["total"]
        for f in activas.filter(tipo="terminal_pos")
        .values("destinatario")
        .annotate(total=Sum("objetivo"))
    }
    transferencia = Cuenta.objects.filter(tipo="transferencia", estado="activa").order_by("pk")
    if destinatarios is None:  # FR-026: sin cuenta de transferencia ni POS Activa (señas), no sale
        destinatarios = Destinatario.objects.visibles().filter(
            pk__in=activas.filter(tipo__in=["transferencia", "terminal_pos"]).values("destinatario")
        )
    destinatarios = destinatarios.order_by("nombre").prefetch_related(
        Prefetch("cuentas", queryset=transferencia, to_attr="cuentas_transferencia")
    )
    filas = []
    for d in destinatarios:
        d.esperado = topes.get(d.pk, CERO) + objetivos.get(d.pk, CERO)
        d.recibido = cobros.get(d.pk, CERO) + pagos.get(d.pk, CERO)
        d.falta = d.esperado - d.recibido
        if not con_falta or d.falta > CERO:
            filas.append(d)
    return filas


def _exigir_admin(usuario):
    if usuario is None or usuario.role != roles.ADMIN:
        raise PermissionDenied


def registrar_pago_complementario(
    *,
    usuario,
    destinatario,
    mes,
    monto,
    metodo,
    cuenta=None,
    comprobante=None,
    observaciones="",
) -> PagoComplementario:
    """Registra un pago fuera del sistema; baja el cupo de la cuenta del mes (FR-005 a FR-015)."""
    _exigir_admin(usuario)
    pago = PagoComplementario(
        destinatario=destinatario,
        mes=mes,
        monto=monto,
        metodo=metodo,
        cuenta=cuenta,
        observaciones=observaciones,
        creado_por=usuario,
    )
    try:
        with transaction.atomic():
            fila()
            _con_archivo(pago, comprobante)
            pago.full_clean()
            medidos = medir([(pago.cuenta, inicio_dia_arg(pago.mes))])
            pago.save()
            registrar_alta(pago, usuario=usuario)
            avisar_cruces(medidos, pago)
    except Exception:
        _borrar_archivo(pago)
        raise
    return pago


CAMPOS_EDITABLES_PAGO = {"destinatario", "mes", "monto", "metodo", "cuenta", "observaciones"}


def editar_pago_complementario(pago, *, usuario, comprobante=None, **datos) -> PagoComplementario:
    """Corrige un pago Registrado; el comprobante anterior queda en disco (FR-018, FR-019)."""
    _exigir_admin(usuario)
    for clave in datos:
        if clave not in CAMPOS_EDITABLES_PAGO:
            raise ValidationError(f"El campo {clave} no se edita.")
    nuevo = None
    try:
        with transaction.atomic():
            fila()
            pago = PagoComplementario.objects.select_for_update().get(pk=pago.pk)
            if pago.estado == PagoComplementario.Estado.ANULADO:
                raise ValidationError("Un pago complementario Anulado no se edita.")
            antes = foto(pago)
            viejo = (pago.cuenta, inicio_dia_arg(pago.mes))
            for clave, valor in datos.items():
                setattr(pago, clave, valor)
            if pago.metodo == PagoComplementario.Metodo.EFECTIVO and "cuenta" not in datos:
                pago.cuenta = None
            _con_archivo(pago, comprobante)
            if comprobante:
                nuevo = pago
            pago.full_clean()
            medidos = medir([viejo, (pago.cuenta, inicio_dia_arg(pago.mes))])
            pago.save()
            registrar_cambio(pago, antes, usuario=usuario)
            avisar_cruces(medidos, pago)
    except Exception:
        if nuevo is not None:
            _borrar_archivo(nuevo)  # solo el archivo que escribió esta llamada (FR-035)
        raise
    return pago


def anular_pago_complementario(pago, *, usuario, motivo, detalle="") -> PagoComplementario:
    """Registrado -> Anulado con motivo; el cupo vuelve (FR-016, FR-017)."""
    _exigir_admin(usuario)
    with transaction.atomic():
        fila()
        pago = PagoComplementario.objects.select_for_update().get(pk=pago.pk)
        if pago.estado == PagoComplementario.Estado.ANULADO:
            raise ValidationError("El pago complementario ya está Anulado.")
        antes = foto(pago)
        pago.estado = PagoComplementario.Estado.ANULADO
        pago.motivo_anulacion = motivo
        pago.detalle_anulacion = detalle
        pago.full_clean()
        pago.save()
        registrar_cambio(pago, antes, usuario=usuario)
    return pago


def estado_visual(cobro) -> tuple[str, list[str]]:
    """Nivel del ícono de estado de la lista y sus motivos (018/FR-028). Solo lee atributos
    anotados (`_anotadas`) e `items.all()` precargado: 0 consultas por fila. Gana el peor nivel."""
    con_error = getattr(cobro, "factura_con_error", False)
    if cobro.estado == Cobro.Estado.CANCELADO:  # solo una factura con error saca al gris
        if con_error:
            return "rojo", ["Factura con error.", "Cancelado."]
        return "gris", ["Cancelado."]
    envios = {i.estado_envio for i in cobro.items.all()}
    Envio = ItemCobro.EstadoEnvio
    rojos = [
        texto
        for hay, texto in (
            (Envio.ERROR in envios, "Error al enviar a Dentalink."),
            (Envio.REQUIERE_AJUSTE in envios, "Requiere ajuste manual en Dentalink."),
            (con_error, "Factura con error."),
        )
        if hay
    ]
    amarillos = [
        texto
        for hay, texto in (
            (
                cobro.estado == Cobro.Estado.PENDIENTE,
                "Pendiente: espera el comprobante y toma cupo.",
            ),
            (cobro.excedente and not cobro.revisado, "Excedente sin revisar."),
            (Envio.PENDIENTE in envios, "Envío a Dentalink pendiente."),
            (Envio.REVISAR in envios, "Revisar en Dentalink."),
            (cobro.faltan_items, "Sin vincular a Dentalink."),
            (getattr(cobro, "factura_a_revisar", False), "Factura a revisar."),
            (getattr(cobro, "factura_pendiente", False), "Factura pendiente de emisión."),
        )
        if hay
    ]
    if rojos:
        return "rojo", [*rojos, *amarillos]
    if amarillos:
        return "amarillo", amarillos
    return "verde", ["Finalizado."]
