import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import re
from collections import defaultdict

from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError
from django.db.models import CheckConstraint
from django.core.management.base import CommandError
from django.core.management.color import no_style
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Min, Sum
from django.db.models.functions import TruncMonth

from django.utils import timezone

from payment_instructions.models import (
    AutomationRule,
    Payment,
    PaymentRecipient,
    RuleExecutionLog,
    User,
)
from sistema.asignacion import servicios as asignacion
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import foto
from sistema.cobros.models import Cobro, ItemCobro, PagoComplementario
from sistema.core.templatetags.pesos import pesos
from sistema.core.tiempo import ARG, rango_mes_arg
from sistema.destinatarios.models import Cuenta, Destinatario, TopeVigente
from sistema.automatizacion.models import Regla
from sistema.dentalink.models import ConfiguracionDentalink
from sistema.usuarios.models import Perfil
from sistema.avisos import servicios as avisos
from sistema.dentalink import servicios as dentalink
from sistema.dentalink.cliente import DentalinkNoDisponible
from sistema.dentalink.models import Banco
from sistema.destinatarios import servicios as destinatarios
from sistema.destinatarios.models import Proveedor
from sistema.terminales import servicios as terminales
from sistema.terminales.models import Terminal

CLAVES = ("para_completar_pagos", "tipos", "vinculos")


def clave_nombre(texto: str) -> str:
    return " ".join(texto.split()).casefold()


def leer_datos(ruta) -> dict:
    try:
        with open(ruta, encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except FileNotFoundError:
        raise CommandError(f"No existe el archivo de datos: {ruta}")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CommandError(f"El archivo de datos no es un JSON válido: {error}")
    if not isinstance(datos, dict):
        raise CommandError("El archivo de datos debe ser un objeto JSON.")
    for clave in CLAVES:
        if clave not in datos:
            raise CommandError(f'Falta la clave "{clave}" en el archivo de datos.')
    if not isinstance(datos["tipos"], dict) or not isinstance(datos["vinculos"], dict):
        raise CommandError('"tipos" y "vinculos" deben ser objetos JSON.')

    for nombre, tipo in datos["tipos"].items():
        if tipo not in Destinatario.Tipo.values:
            raise CommandError(f'Tipo inválido "{tipo}" para el destinatario "{nombre}".')
    for username, vinculo in datos["vinculos"].items():
        usuario = User.objects.filter(username=username).first()
        if usuario is None:
            raise CommandError(f'El vínculo apunta a un usuario que no existe: "{username}".')
        if usuario.role != User.PROFESSIONAL:
            raise CommandError(f'El usuario "{username}" no es especialista.')
        id_ = vinculo.get("id") if isinstance(vinculo, dict) else None
        nombre = vinculo.get("nombre") if isinstance(vinculo, dict) else None
        if not isinstance(id_, int) or isinstance(id_, bool):
            raise CommandError(f'El vínculo de "{username}" necesita un "id" entero.')
        if not isinstance(nombre, str) or not nombre.strip():
            raise CommandError(f'El vínculo de "{username}" necesita un "nombre".')
    completar = datos["para_completar_pagos"]
    if not isinstance(completar, str) or not User.objects.filter(username=completar).exists():
        raise CommandError(f'El usuario para completar pagos no existe: "{completar}".')

    datos["tipos"] = {clave_nombre(nombre): tipo for nombre, tipo in datos["tipos"].items()}
    return datos


@dataclass
class Informe:
    modo: str
    grupos: dict[str, list[str]] = field(default_factory=dict)
    conflictos: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    sin_vinculo: list[str] = field(default_factory=list)
    sin_comprobante: list[int] = field(default_factory=list)
    totales: dict[str, int] = field(default_factory=dict)
    montos: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    diferencias: list[str] | None = None

    @property
    def bloquea(self) -> bool:
        return bool(self.conflictos or self.errores)

    def texto(self) -> str:
        t = self.totales
        lineas = [f"Modo: {self.modo}", f"Destinatarios: {len(self.grupos)}"]
        lineas += [f"  {nombre}: {', '.join(cuentas)}" for nombre, cuentas in self.grupos.items()]
        lineas.append(f"Conflictos: {len(self.conflictos)}")
        lineas += [f"  {c}" for c in self.conflictos]
        lineas.append(f"Errores: {len(self.errores)}")
        lineas += [f"  {e}" for e in self.errores]
        lineas.append(f"Especialistas sin vínculo a Dentalink: {len(self.sin_vinculo)}")
        lineas += [f"  {u}" for u in self.sin_vinculo]
        lineas.append(f"Comprobantes que no están: {len(self.sin_comprobante)}")
        lineas += [f"  Cobro {pk}" for pk in self.sin_comprobante]
        lineas += [
            "Totales",
            f"  Cobros: {t.get('cobros', 0)} | Señas: {t.get('senas', 0)}"
            f" | Pagos complementarios: {t.get('pagos_complementarios', 0)}",
            f"  Cuentas: {t.get('cuentas', 0)} | Usuarios: {t.get('usuarios', 0)}"
            f" | Reglas: {t.get('reglas', 0)}"
            f" | Ejecuciones de reglas: {t.get('ejecuciones', 0)}",
            "Montos por cuenta y mes (prod)",
        ]
        lineas += [
            f"  {cuenta} {mes}: {pesos(m)}" for (cuenta, mes), m in sorted(self.montos.items())
        ]
        if self.diferencias is None:
            motivo = "No corre: hay conflictos o errores" if self.bloquea else "No corrió"
            lineas.append(f"Verificación: {motivo}")
        elif not self.diferencias:
            lineas.append("Verificación: 0 diferencias")
        else:
            lineas.append(f"Verificación: {len(self.diferencias)} diferencias")
            lineas += [f"  {d}" for d in self.diferencias]
        return "\n".join(lineas)


def _nombre_fila(fila) -> str:
    return fila.alias or fila.cbu or f"fila {fila.pk}"


def _estado_fila(fila) -> str:
    if fila.recipient_type == PaymentRecipient.DEPOSIT_ONLY:
        return "Archivada"
    return "Activa" if fila.is_active else "Inactiva"


def _grupos() -> dict[str, list[str]]:
    filas = defaultdict(list)
    for fila in PaymentRecipient.objects.order_by("created_at", "pk"):
        filas[clave_nombre(fila.name)].append(fila)
    return {
        grupo[0].name.strip(): [f"{_nombre_fila(f)} ({_estado_fila(f)})" for f in grupo]
        for grupo in filas.values()
    }


def _lista_pks(pks) -> str:
    pks = [str(pk) for pk in pks]
    return pks[0] if len(pks) == 1 else f"{', '.join(pks[:-1])} y {pks[-1]}"


def _conflictos() -> list[str]:
    alias, cbus = defaultdict(list), defaultdict(list)
    for fila in PaymentRecipient.objects.order_by("pk"):
        if fila.alias and fila.alias.strip():
            alias[fila.alias.strip().casefold()].append(fila)
        if fila.cbu and fila.cbu.strip():
            cbus[fila.cbu.strip()].append(fila)
    lineas = []
    for grupo in alias.values():
        if len(grupo) > 1:
            lineas.append(f'Alias "{grupo[0].alias}": filas {_lista_pks(f.pk for f in grupo)}')
    for cbu, grupo in cbus.items():
        if len(grupo) > 1:
            lineas.append(f'CBU "{cbu}": filas {_lista_pks(f.pk for f in grupo)}')
    return lineas


def _errores_de_forma(datos) -> list[str]:
    completar = datos["para_completar_pagos"]
    errores = []
    filas = list(PaymentRecipient.objects.order_by("pk"))
    posicion = _posiciones(sorted(filas, key=lambda f: (f.priority_order, f.pk)))
    for fila in filas:
        antes = len(errores)
        if not (fila.alias or "").strip() and not (fila.cbu or "").strip():
            errores.append(f"Fila {fila.pk}: no tiene alias ni CBU")
        if fila.alias and len(fila.alias) > 60:
            errores.append(f"Fila {fila.pk}: el alias tiene más de 60 caracteres")
        if fila.cbu and not re.fullmatch(r"\d{22}", fila.cbu):
            errores.append(f"Fila {fila.pk}: el CBU no tiene 22 dígitos")
        if len(errores) > antes:
            continue
        # Las constraints de Cuenta (mínimo, tope semanal, etc.): lo que haría fallar a migrar.
        try:
            cuenta = _armar_cuenta(fila, None, posicion.get(fila.pk))
            cuenta.clean_fields(exclude=["destinatario"])
            # Solo los CHECK: los UNIQUE chocarían con cuentas ya migradas (segunda corrida).
            for c in Cuenta._meta.constraints:
                if isinstance(c, CheckConstraint):
                    c.validate(Cuenta, cuenta, exclude={"destinatario"})
        except ValidationError as e:
            errores += [f"Fila {fila.pk}: {m}" for m in e.messages]
    pagos = Payment.objects.select_related("payment_recipient", "professional").order_by("pk")
    for pago in pagos:
        if pago.amount == 0:
            errores.append(f"Cobro {pago.pk}: el monto es 0")
        fila = pago.payment_recipient
        if fila is None:
            errores.append(f"Cobro {pago.pk}: no tiene destinatario")
        if pago.metodo != Payment.TRANSFERENCIA or pago.estado != Payment.COMPLETADO:
            errores.append(f"Cobro {pago.pk}: no es una transferencia completada")
        if fila is not None and pago.professional.username != completar:
            es_sena = pago.payment_type == Payment.DEPOSIT
            if es_sena and fila.recipient_type == PaymentRecipient.REGULAR:
                errores.append(f"Cobro {pago.pk}: es una seña en un destinatario regular")
            if not es_sena and fila.recipient_type == PaymentRecipient.DEPOSIT_ONLY:
                errores.append(f"Cobro {pago.pk}: es un pago regular en un destinatario de señas")
    roles = (User.ADMINISTRATOR, User.OPERATOR, User.PROFESSIONAL)
    for u in User.objects.exclude(role__in=roles).order_by("pk"):
        errores.append(f'Usuario "{u.username}": el rol "{u.role}" no existe en el sistema nuevo')
    return errores


def _sin_vinculo(datos) -> list[str]:
    usernames = User.objects.filter(role=User.PROFESSIONAL).values_list("username", flat=True)
    excluidos = {datos["para_completar_pagos"], *datos["vinculos"]}
    return sorted(u for u in usernames if u not in excluidos)


def _sin_comprobante() -> list[int]:
    pagos = Payment.objects.exclude(proof_of_payment_file="").order_by("pk")
    return [
        pk
        for pk, nombre in pagos.values_list("pk", "proof_of_payment_file")
        if not default_storage.exists(nombre)
    ]


def _totales(datos) -> dict[str, int]:
    pagos = Payment.objects.all()
    de_completar = pagos.filter(professional__username=datos["para_completar_pagos"])
    propios = pagos.exclude(professional__username=datos["para_completar_pagos"])
    return {
        "cobros": propios.filter(payment_type=Payment.REGULAR).count(),
        "senas": propios.filter(payment_type=Payment.DEPOSIT).count(),
        "pagos_complementarios": de_completar.count(),
        "cuentas": PaymentRecipient.objects.count(),
        "usuarios": User.objects.exclude(username=datos["para_completar_pagos"]).count(),
        "reglas": AutomationRule.objects.count(),
        "ejecuciones": RuleExecutionLog.objects.count(),
    }


def _montos() -> dict[tuple[str, str], Decimal]:
    filas = (
        Payment.objects.filter(payment_recipient__isnull=False)
        .annotate(mes=TruncMonth("created_at", tzinfo=ARG))
        .values("payment_recipient", "payment_recipient__alias", "payment_recipient__cbu", "mes")
        .annotate(total=Sum("amount"))
        .order_by()
    )
    return {
        (
            f["payment_recipient__alias"] or f["payment_recipient__cbu"],
            f["mes"].astimezone(ARG).strftime("%Y-%m"),
        ): Decimal(f["total"]).quantize(Decimal("0.01"))
        for f in filas
    }


def revisar(datos) -> Informe:
    """Solo lectura: arma el informe de prod."""
    return Informe(
        modo="revisión",
        grupos=_grupos(),
        conflictos=_conflictos(),
        errores=_errores_de_forma(datos),
        sin_vinculo=_sin_vinculo(datos),
        sin_comprobante=_sin_comprobante(),
        totales=_totales(datos),
        montos=_montos(),
    )


ORIGEN = "migración de prod"


def _alta(obj) -> Evento:
    """Evento de alta igual a `registrar_alta`, sin guardar (R8)."""
    return Evento(
        tipo=Evento.Tipo.ALTA,
        fecha=timezone.now(),
        usuario=None,
        origen=ORIGEN,
        entidad=obj._meta.label_lower,
        entidad_id=obj.pk,
        cambios=[{"campo": k, "anterior": None, "nuevo": v} for k, v in sorted(foto(obj).items())],
    )


def _guardar(obj, eventos):
    obj.full_clean()
    obj.save()
    eventos.append(_alta(obj))


def _destinatarios(datos) -> dict[str, Destinatario]:
    """Un destinatario por grupo de filas con el mismo nombre normalizado (FR-006, FR-007)."""
    grupos = defaultdict(list)
    for fila in PaymentRecipient.objects.order_by("created_at", "pk"):
        grupos[clave_nombre(fila.name)].append(fila)
    resultado, eventos = {}, []
    for clave, filas in grupos.items():
        activas = [f for f in filas if f.recipient_type == "REGULAR" and f.is_active]
        destinatario = Destinatario(
            nombre=filas[0].name.strip(),
            tipo=datos["tipos"].get(clave, "profesional"),
            estado=Destinatario.Estado.ACTIVO if activas else Destinatario.Estado.INACTIVO,
            fecha_activacion=min(f.created_at for f in filas) if activas else None,
        )
        _guardar(destinatario, eventos)
        resultado[clave] = destinatario
    Evento.objects.bulk_create(eventos, batch_size=1000)
    return resultado


def _posiciones(filas) -> dict[int, int]:
    """Posición en la lista de prioridad de las filas regulares activas (FR-009)."""
    lista = [f for f in filas if f.recipient_type == "REGULAR" and f.is_active]
    return {f.pk: i for i, f in enumerate(lista, start=1)}


def _armar_cuenta(fila, destinatario, posicion) -> Cuenta:
    """La Cuenta en memoria de una fila vieja; la usan `_cuentas` y la revisión de forma."""
    sena = fila.recipient_type == "DEPOSIT_ONLY"
    estado = "archivada" if sena else "activa" if fila.is_active else "inactiva"
    return Cuenta(
        destinatario=destinatario,
        tipo="transferencia",
        alias=(fila.alias or "").strip() or None,
        cbu=(fila.cbu or "").strip() or None,
        tope_mensual=Decimal(fila.max_amount),
        tope_semanal=Decimal(fila.weekly_cap_amount),
        minimo_por_pago=Decimal(fila.min_threshold),
        observaciones=fila.notes or "",
        estado=estado,
        fecha_activacion=fila.created_at if estado == "activa" else None,
        fecha_archivado=timezone.now() if sena else None,
        posicion=posicion,
    )


def _cuentas(destinatarios) -> dict[int, Cuenta]:
    """Una cuenta por fila vieja; la lista de prioridad queda en 1..n sin huecos (FR-009)."""
    filas = list(PaymentRecipient.objects.order_by("priority_order", "pk"))
    lista = [f for f in filas if f.recipient_type == "REGULAR" and f.is_active]
    posicion = _posiciones(filas)
    resultado, eventos = {}, []
    # Las activas de la lista primero: sus pk siguen el orden de la lista.
    for fila in lista + [f for f in filas if f.pk not in posicion]:
        cuenta = _armar_cuenta(fila, destinatarios[clave_nombre(fila.name)], posicion.get(fila.pk))
        _guardar(cuenta, eventos)
        resultado[fila.pk] = cuenta
    Evento.objects.bulk_create(eventos, batch_size=1000)
    # No-op si la lista ya está sin huecos; es la red de seguridad de 004.
    asignacion.sincronizar_lista(usuario=None, origen=ORIGEN)
    return resultado


def _pagos_complementarios(datos, cuentas) -> None:
    """Un pago complementario Registrado por pago de "para completar pagos" (FR-019, R8).

    bulk_create no pasa por los servicios de 008: no se avisa excedente (009).
    """
    pagos = Payment.objects.filter(professional__username=datos["para_completar_pagos"])
    nuevos = []
    for p in pagos.order_by("pk"):
        cuenta = cuentas[p.payment_recipient_id]
        nombre = p.proof_of_payment_file.name if p.proof_of_payment_file else ""
        nuevos.append(
            PagoComplementario(
                destinatario=cuenta.destinatario,
                cuenta=cuenta,
                mes=rango_mes_arg(p.created_at)[0].date(),
                monto=Decimal(p.amount),
                metodo=PagoComplementario.Metodo.TRANSFERENCIA,
                creado_por_id=p.operator_user_id,
                observaciones=p.observaciones or "",
                estado=PagoComplementario.Estado.REGISTRADO,
                comprobante=nombre if nombre and default_storage.exists(nombre) else "",
            )
        )
    PagoComplementario.objects.bulk_create(nuevos, batch_size=1000)
    Evento.objects.bulk_create([_alta(n) for n in nuevos], batch_size=1000)


def _cobros(datos, cuentas) -> dict[int, Cobro]:
    """Un Cobro finalizado por pago viejo, con el mismo número (FR-012, R5, R6)."""
    pagos = Payment.objects.exclude(professional__username=datos["para_completar_pagos"])
    cobros = []
    for p in pagos.order_by("pk"):
        cuenta = cuentas[p.payment_recipient_id]
        nombre = p.proof_of_payment_file.name if p.proof_of_payment_file else ""
        cobros.append(
            Cobro(
                pk=p.pk,
                cuenta=cuenta,
                destinatario=cuenta.destinatario,
                monto=Decimal(p.amount),
                estado=Cobro.Estado.FINALIZADO,
                metodo=Cobro.Metodo.TRANSFERENCIA,
                fecha=p.created_at,
                creado_por_id=p.operator_user_id,
                nombre_paciente=p.patient_name,
                observaciones=p.observaciones or "",
                revisado=p.reviewed,
                excedente=False,
                migrado=True,
                comprobante=nombre if nombre and default_storage.exists(nombre) else "",
            )
        )
    Cobro.objects.bulk_create(cobros, batch_size=1000)
    # auto_now_add pisa fecha_alta con ahora; la ventana de 15 días del operador (002/FR-011)
    # cuenta desde la creación original.
    Cobro.objects.filter(migrado=True).update(fecha_alta=F("fecha"))
    for c in cobros:
        c.fecha_alta = c.fecha
    Evento.objects.bulk_create([_alta(c) for c in cobros], batch_size=1000)
    return {c.pk: c for c in cobros}


def _topes(cuentas) -> None:
    """Primer tope mensual y semanal de cada cuenta, con los valores de prod (FR-027).
    Rige desde el cobro migrado más antiguo o, sin cobros, desde el alta. Sin evento."""
    primero = dict(
        Cobro.objects.filter(migrado=True).values_list("cuenta").annotate(m=Min("fecha"))
    )
    topes = [
        TopeVigente(
            cuenta=c,
            tipo=t,
            monto=getattr(c, f"tope_{t}"),
            desde=primero.get(c.pk, c.fecha_alta),
        )
        for c in cuentas.values()
        for t in TopeVigente.Tipo.values
    ]
    TopeVigente.objects.bulk_create(topes, batch_size=1000)


def _items(datos, cobros) -> None:
    """Un ítem histórico por cobro: nunca se envía a Dentalink (FR-013 a FR-015, R8)."""
    config = ConfiguracionDentalink.objects.filter(pk=1).first()
    con_prestacion = config is not None and config.id_prestacion_saldo_a_favor is not None
    pagos = Payment.objects.select_related("professional").filter(pk__in=cobros).order_by("pk")
    items = []
    for p in pagos:
        cobro, prof = cobros[p.pk], p.professional
        sena = p.payment_type == Payment.DEPOSIT
        item = ItemCobro(
            cobro=cobro,
            numero=1,
            monto=cobro.monto,
            estado_envio=ItemCobro.EstadoEnvio.HISTORICO,
            nombre_profesional=prof.get_full_name() or prof.username,
            id_profesional=datos["vinculos"].get(prof.username, {}).get("id"),
            saldo_a_favor=sena,
        )
        if sena:
            item.id_prestacion = config.id_prestacion_saldo_a_favor if con_prestacion else None
            item.nombre_prestacion = (
                config.nombre_prestacion_saldo_a_favor if con_prestacion else "Saldo a favor"
            )
        items.append(item)
    ItemCobro.objects.bulk_create(items, batch_size=1000)
    Evento.objects.bulk_create([_alta(i) for i in items], batch_size=1000)


def _usuarios(datos) -> None:
    """Los usuarios no se copian (misma tabla): se ajusta rol, estado y Perfil (FR-020 a FR-022)."""
    eventos = []
    for u in User.objects.order_by("pk"):
        if u.username == datos["para_completar_pagos"]:
            if u.is_active:
                User.objects.filter(pk=u.pk).update(is_active=False)
            continue
        if u.is_superuser and u.role != User.ADMINISTRATOR:
            u.role = User.ADMINISTRATOR
            u.save(update_fields=["role"])
        # T040: en prod 003 ya creó el Perfil; unique de usuario no admite un segundo.
        perfil, _ = Perfil.objects.get_or_create(usuario=u)
        perfil.fecha_activacion = u.date_joined if u.is_active else None
        vinculo = datos["vinculos"].get(u.username)
        if u.role == User.PROFESSIONAL and vinculo:
            perfil.id_profesional_dentalink = vinculo["id"]
            perfil.nombre_profesional_dentalink = vinculo["nombre"]
        perfil.full_clean()
        perfil.save()
        alta = _alta(u)
        alta.cambios += _alta(perfil).cambios
        eventos.append(alta)
    Evento.objects.bulk_create(eventos, batch_size=1000)


ACCIONES = {"FIRST": "primero", "LAST": "ultimo", "MIDDLE": "medio", "SPECIFIC": "posicion"}


def _reglas(cuentas) -> dict[int, Regla]:
    """Una Regla por regla vieja, con su última ejecución: no corre dos veces el mismo día."""
    resultado, eventos = {}, []
    for r in AutomationRule.objects.select_related("payment_recipient").order_by("pk"):
        semana = r.rule_type == AutomationRule.RuleType.WEEKDAY
        if r.action_type == AutomationRule.ActionType.SET_PRIORITY:
            accion = ACCIONES[r.priority_position]
        else:
            accion = "activar" if r.active_value else "desactivar"
        activa = r.is_active and r.payment_recipient.recipient_type == "REGULAR"
        regla = Regla(
            nombre=r.name,
            cuenta=cuentas[r.payment_recipient_id],
            estado="activa" if activa else "inactiva",
            fecha_activacion=r.created_at if activa else None,
            disparador="semana" if semana else "mes",
            dia=r.trigger_weekday + 1 if semana else r.trigger_monthday,
            hora=r.trigger_time,
            accion=accion,
            posicion=r.priority_value if accion == "posicion" else None,
            ultima_ejecucion=r.last_executed,
        )
        _guardar(regla, eventos)
        resultado[r.pk] = regla
    Evento.objects.bulk_create(eventos, batch_size=1000)
    return resultado


def _ejecuciones(reglas) -> None:
    """Cada ejecución vieja es un evento de ejecución con su fecha original (FR-024)."""
    eventos = []
    for log in RuleExecutionLog.objects.order_by("pk"):
        regla = reglas[log.rule_id]
        campo = "estado" if regla.accion in ("activar", "desactivar") else "posicion"
        detalle = log.action_taken + (f": {log.error_message}" if log.error_message else "")
        eventos.append(
            Evento(
                tipo=Evento.Tipo.EJECUCION_REGLA,
                fecha=log.executed_at,
                usuario=None,
                origen=f"regla '{regla.nombre}'",
                entidad="automatizacion.regla",
                entidad_id=regla.pk,
                exito=log.success,
                detalle=detalle,
                cambios=[{"campo": campo, "anterior": log.old_value, "nuevo": log.new_value}],
            )
        )
    Evento.objects.bulk_create(eventos, batch_size=1000)


def _clave_cuenta(alias, cbu) -> str:
    return (alias or "").strip().lower() or (cbu or "").strip()


def _montos_viejos() -> dict[tuple[str, str], Decimal]:
    filas = (
        Payment.objects.annotate(mes=TruncMonth("created_at", tzinfo=ARG))
        .values("payment_recipient__alias", "payment_recipient__cbu", "mes")
        .annotate(total=Sum("amount"))
        .order_by()
    )
    return {
        (
            _clave_cuenta(f["payment_recipient__alias"], f["payment_recipient__cbu"]),
            _mes(f["mes"]),
        ): (Decimal(f["total"]).quantize(Decimal("0.01")))
        for f in filas
    }


def _mes(valor) -> str:
    if hasattr(valor, "astimezone"):
        valor = valor.astimezone(ARG)
    return valor.strftime("%Y-%m")


def _montos_nuevos() -> dict[tuple[str, str], Decimal]:
    cobros = (
        Cobro.objects.filter(migrado=True)
        .annotate(mes=TruncMonth("fecha", tzinfo=ARG))
        .values("cuenta__alias", "cuenta__cbu", "mes")
        .annotate(total=Sum("monto"))
        .order_by()
    )
    complementarios = (
        PagoComplementario.objects.values("cuenta__alias", "cuenta__cbu", "mes")
        .annotate(total=Sum("monto"))
        .order_by()
    )
    resultado = defaultdict(Decimal)
    for f in [*cobros, *complementarios]:
        clave = (_clave_cuenta(f["cuenta__alias"], f["cuenta__cbu"]), _mes(f["mes"]))
        resultado[clave] += f["total"]
    return {k: v.quantize(Decimal("0.01")) for k, v in resultado.items()}


def verificar(datos) -> list[str]:
    """FR-026: compara prod con lo migrado. Una línea por diferencia; vacía = todo igual."""
    viejo = _totales(datos)
    migrados = Cobro.objects.filter(migrado=True)
    nuevo = {
        "cobros": migrados.exclude(items__saldo_a_favor=True).count(),
        "senas": migrados.filter(items__saldo_a_favor=True).distinct().count(),
        "pagos_complementarios": PagoComplementario.objects.count(),
        "cuentas": Cuenta.objects.filter(tipo="transferencia").count(),
        "usuarios": Perfil.objects.count(),
        "reglas": Regla.objects.count(),
        "ejecuciones": Evento.objects.filter(tipo=Evento.Tipo.EJECUCION_REGLA).count(),
    }
    nombres = {
        "cobros": "Cobros",
        "senas": "Señas",
        "pagos_complementarios": "Pagos complementarios",
        "cuentas": "Cuentas",
        "usuarios": "Usuarios",
        "reglas": "Reglas",
        "ejecuciones": "Ejecuciones de reglas",
    }
    lineas = [
        f"{nombre}: prod {viejo[k]}, nuevo {nuevo[k]}"
        for k, nombre in nombres.items()
        if viejo[k] != nuevo[k]
    ]
    cero = Decimal("0.00")
    antes, despues = _montos_viejos(), _montos_nuevos()
    for cuenta, mes in sorted(antes.keys() | despues.keys()):
        a, d = antes.get((cuenta, mes), cero), despues.get((cuenta, mes), cero)
        if a != d:
            lineas.append(f'Cuenta "{cuenta}", {mes}: prod {pesos(a)}, nuevo {pesos(d)}')
    return lineas


def migrar(datos, *, definitiva) -> Informe:
    # FR-032: antes de cargar nada, también en revisión.
    if Cobro.objects.exists() or Cuenta.objects.filter(tipo="transferencia").exists():
        raise CommandError("El sistema nuevo ya tiene datos. La migración corre una sola vez.")
    informe = revisar(datos)
    informe.modo = "definitiva" if definitiva else "revisión"
    if informe.bloquea:
        if definitiva:
            raise CommandError("La migración no corre: hay conflictos o errores.")
        return informe
    try:
        with transaction.atomic():
            asignacion.fila()
            _usuarios(datos)
            cuentas = _cuentas(_destinatarios(datos))
            _pagos_complementarios(datos, cuentas)
            cobros = _cobros(datos, cuentas)
            _topes(cuentas)
            _items(datos, cobros)
            _ejecuciones(_reglas(cuentas))
            # Las constraints diferidas (posición) se validan acá, también en revisión.
            connection.check_constraints()
            informe.diferencias = verificar(datos)
            if definitiva and informe.diferencias:
                raise CommandError(
                    "La verificación encontró diferencias:\n" + "\n".join(informe.diferencias)
                )
            if definitiva:
                # FR-018: los cobros nuevos siguen después del número más alto migrado.
                with connection.cursor() as cursor:
                    for sql in connection.ops.sequence_reset_sql(no_style(), [Cobro]):
                        cursor.execute(sql)
            else:
                transaction.set_rollback(True)
    except ValidationError as error:
        raise CommandError("\n".join(error.messages))
    except IntegrityError as error:
        raise CommandError(str(error))
    return informe


CLAVES_CONFIGURACION = (
    "destinatarios",
    "efectivo",
    "terminales",
    "cuentas_pos",
    "dentalink",
    "avisos",
)
METODOS = ("credito", "debito", "qr", "link")


def leer_configuracion(ruta) -> dict:
    try:
        with open(ruta, encoding="utf-8") as archivo:
            conf = json.load(archivo)
    except FileNotFoundError:
        raise CommandError(f"No existe el archivo de datos: {ruta}")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CommandError(f"El archivo de datos no es un JSON válido: {error}")
    if not isinstance(conf, dict):
        raise CommandError("El archivo de datos debe ser un objeto JSON.")
    for clave in CLAVES_CONFIGURACION:
        if clave not in conf:
            raise CommandError(f'Falta la clave "{clave}" en el archivo de configuración.')
    for clave in ("destinatarios", "terminales", "cuentas_pos"):
        if not isinstance(conf[clave], list):
            raise CommandError(f'"{clave}" debe ser una lista.')
    return conf


def _destinatario(nombre, ruta):
    clave = clave_nombre(str(nombre))
    coinciden = [d for d in Destinatario.objects.all() if clave_nombre(d.nombre) == clave]
    if not coinciden:
        raise CommandError(f'{ruta}: no existe el destinatario "{nombre}"')
    if len(coinciden) > 1:
        raise CommandError(f'{ruta}: hay {len(coinciden)} destinatarios con el nombre "{nombre}"')
    return coinciden[0]


def _proveedor(nombre, ruta):
    clave = clave_nombre(str(nombre))
    coinciden = [p for p in Proveedor.objects.all() if clave_nombre(p.nombre) == clave]
    if not coinciden:
        raise CommandError(f'{ruta}: no existe el proveedor "{nombre}"')
    if len(coinciden) > 1:
        raise CommandError(f'{ruta}: hay {len(coinciden)} proveedores con el nombre "{nombre}"')
    return coinciden[0]


def cargar_configuracion(conf, *, stdout):
    """FR-010: todo o nada, una sola vez. Devuelve las cantidades cargadas."""
    if Cuenta.objects.filter(tipo="efectivo").exists() or Terminal.objects.exists():
        raise CommandError("La configuración inicial ya está cargada.")
    copia_ok = True
    try:
        dentalink.actualizar_copia(usuario=None)
    except DentalinkNoDisponible:
        copia_ok = False
        stdout.write(
            "Aviso: Dentalink no respondió. El banco por defecto queda vacío; "
            "cargalo desde la pantalla de Dentalink."
        )
    ruta = "configuracion"
    cantidades = {"terminales": 0, "cuentas_pos": 0, "sesiones": 0, "direcciones": 0}
    try:
        with transaction.atomic():
            for i, dato in enumerate(conf["destinatarios"]):
                ruta = f"destinatarios[{i}]"
                nombre, tipo = dato["nombre"], dato["tipo"]
                if tipo not in Destinatario.Tipo.values:
                    raise CommandError(f'{ruta}.tipo: tipo inválido "{tipo}"')
                existe = any(
                    clave_nombre(d.nombre) == clave_nombre(nombre)
                    for d in Destinatario.objects.all()
                )
                if not existe:
                    destinatarios.crear_destinatario(usuario=None, nombre=nombre, tipo=tipo)

            ruta = "efectivo"
            efectivo = conf["efectivo"]
            destinatarios.crear_cuenta(
                usuario=None,
                tipo="efectivo",
                destinatario=_destinatario(efectivo["destinatario"], "efectivo.destinatario"),
                titular=efectivo.get("titular", ""),
            )

            for i, dato in enumerate(conf["terminales"]):
                ruta = f"terminales[{i}]"
                metodos = dato["metodos"]
                for m in metodos:
                    if m not in METODOS:
                        raise CommandError(f'{ruta}.metodos: método desconocido "{m}"')
                terminales.crear_terminal(
                    usuario=None,
                    nombre=dato["nombre"],
                    proveedor=_proveedor(dato["proveedor"], f"{ruta}.proveedor"),
                    **{f"acepta_{m}": m in metodos for m in METODOS},
                )
                cantidades["terminales"] += 1

            for i, dato in enumerate(conf["cuentas_pos"]):
                ruta = f"cuentas_pos[{i}]"
                objetivo = dato["objetivo"]
                if not isinstance(objetivo, str):
                    raise CommandError(
                        f"cuentas_pos[{i}].objetivo: escribí el monto como texto, "
                        'por ejemplo "500000.00"'
                    )
                try:
                    objetivo = Decimal(objetivo)
                except InvalidOperation:
                    raise CommandError(f'{ruta}.objetivo: monto inválido "{objetivo}"')
                cuenta = destinatarios.crear_cuenta(
                    usuario=None,
                    tipo="terminal_pos",
                    destinatario=_destinatario(dato["destinatario"], f"{ruta}.destinatario"),
                    proveedor=_proveedor(dato["proveedor"], f"{ruta}.proveedor"),
                    titular=dato.get("titular", ""),
                    objetivo=objetivo,
                )
                cantidades["cuentas_pos"] += 1
                if dato.get("sesion_en"):
                    terminal = Terminal.objects.filter(nombre__iexact=dato["sesion_en"]).first()
                    if terminal is None:
                        raise CommandError(
                            f'{ruta}.sesion_en: no existe la terminal "{dato["sesion_en"]}"'
                        )
                    terminales.abrir_sesion(terminal, cuenta, timezone.now(), usuario=None)
                    cantidades["sesiones"] += 1

            ruta = "dentalink"
            d = conf["dentalink"]
            id_banco = d["banco_por_defecto"]
            banco = None
            if id_banco is not None and copia_ok:
                banco = Banco.objects.filter(id_dentalink=id_banco, vigente=True).first()
                if banco is None:
                    raise CommandError(
                        f"dentalink.banco_por_defecto: no existe el banco {id_banco} "
                        "en la copia de Dentalink"
                    )
            dentalink.guardar_configuracion(
                usuario=None,
                id_prestacion_saldo_a_favor=d["id_prestacion_saldo_a_favor"],
                nombre_prestacion_saldo_a_favor=d["nombre_prestacion_saldo_a_favor"],
                banco_por_defecto=banco,
            )

            ruta = "avisos"
            for email in conf["avisos"]["direcciones"]:
                avisos.agregar_direccion(usuario=None, email=email)
                cantidades["direcciones"] += 1
            avisos.guardar_margen(usuario=None, margen=conf["avisos"]["margen"])
    except ValidationError as error:
        raise CommandError(f"{ruta}: " + "\n".join(error.messages))
    except KeyError as error:
        raise CommandError(f"{ruta}: falta el dato {error}")
    return cantidades
