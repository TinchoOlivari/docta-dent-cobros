from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, CharField, Deferrable, F, Q, Value, When
from django.db.models.functions import Coalesce, Concat, Lower, NullIf, Right

from sistema.core.models import MontoField, Registro, RegistroQuerySet

CUIT_RE = r"^\d{11}$"
CBU_RE = r"^\d{22}$"


class Proveedor(Registro):
    nombre = models.CharField(max_length=60)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("nombre"),
                name="proveedor_nombre_unico",
                violation_error_message="Ya existe un proveedor con ese nombre.",
            ),
        ]

    def __str__(self):
        return self.nombre


class DestinatarioQuerySet(RegistroQuerySet):
    def visibles(self):
        return self.exclude(estado="archivado").order_by("nombre")


class Destinatario(Registro):
    class Tipo(models.TextChoices):
        PROFESIONAL = "profesional", "Profesional"
        PROVEEDOR = "proveedor", "Proveedor"
        EMPLEADO = "empleado", "Empleado"
        CLINICA = "clinica", "Clínica"

    class Estado(models.TextChoices):
        ACTIVO = "activo", "Activo"
        INACTIVO = "inactivo", "Inactivo"
        ARCHIVADO = "archivado", "Archivado"

    nombre = models.CharField(max_length=120)
    cuit = models.CharField(max_length=11, null=True, blank=True)
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ACTIVO)
    fecha_activacion = models.DateTimeField(null=True, blank=True)
    fecha_archivado = models.DateTimeField(null=True, blank=True)
    usuario_profesional = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="destinatario",
    )
    facturacion_activa = models.BooleanField(default=False)
    emisor = models.ForeignKey(
        "arca.Emisor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="destinatarios",
    )

    objects = DestinatarioQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(cuit__isnull=True) | Q(cuit__regex=CUIT_RE),
                name="destinatario_cuit_formato",
                violation_error_message="El CUIT o CUIL debe tener 11 dígitos, solo números.",
            ),
            models.UniqueConstraint(
                fields=["cuit"],
                name="destinatario_cuit_unico",
                violation_error_message="Ya existe un destinatario con ese CUIT o CUIL.",
            ),
            models.CheckConstraint(
                condition=Q(facturacion_activa=False) | Q(emisor__isnull=False),
                name="destinatario_facturacion_con_emisor",
                violation_error_message="Elegí un emisor para activar la facturación.",
            ),
        ]

    def __str__(self):
        return self.nombre

    def clean(self):
        if not self.cuit:
            self.cuit = None
        if self.usuario_profesional and self.usuario_profesional.role != "professional":
            raise ValidationError(
                {"usuario_profesional": "Solo se vincula un usuario Profesional."}
            )
        if self.facturacion_activa and self.emisor and self.emisor.estado != "activo":
            previo = (
                Destinatario.objects.filter(pk=self.pk)
                .values("facturacion_activa", "emisor_id")
                .first()
            )
            if previo != {"facturacion_activa": True, "emisor_id": self.emisor_id}:
                raise ValidationError({"emisor": "Elegí un emisor Activo."})


class CuentaQuerySet(RegistroQuerySet):
    def habilitadas(self):
        return self.filter(estado="activa", destinatario__estado="activo")

    def para_selector(self):
        """Orden de los selectores (022/FR-020): destinatario, texto que ve el usuario, pk.
        `nombre_selector` repite las reglas de `Cuenta.__str__` sin el destinatario."""
        texto = CharField()
        nombre = Case(
            When(
                tipo="transferencia",
                then=Coalesce(
                    NullIf("alias", Value("")),
                    Concat(Value("CBU terminado en "), Right("cbu", 4), output_field=texto),
                ),
            ),
            When(tipo="terminal_pos", then=Coalesce(NullIf("titular", Value("")), Value("POS"))),
            default=Value("Efectivo"),
            output_field=texto,
        )
        return self.annotate(nombre_selector=nombre).order_by(
            "destinatario__nombre", "nombre_selector", "pk"
        )


_MONTOS = ("tope_mensual", "tope_semanal", "minimo_por_pago", "objetivo")
_TRANSF = Q(tipo="transferencia")
_POS = Q(tipo="terminal_pos")
_EFECTIVO = Q(tipo="efectivo")


def _nulos(*campos):
    q = Q()
    for c in campos:
        q &= Q(**{f"{c}__isnull": True})
    return q


def _no_nulos(*campos):
    q = Q()
    for c in campos:
        q &= Q(**{f"{c}__isnull": False})
    return q


def _si(tipo, cond):
    """Regla que solo aplica a un tipo de cuenta: tipo distinto o se cumple la condición."""
    return ~tipo | cond


def _montos_ok():
    q = Q()
    for m in _MONTOS:
        q &= Q(**{f"{m}__isnull": True}) | Q(**{f"{m}__gte": 0})
    return q


def _check(cond, name, msg):
    return models.CheckConstraint(condition=cond, name=name, violation_error_message=msg)


class Cuenta(Registro):
    class Tipo(models.TextChoices):
        EFECTIVO = "efectivo", "Efectivo"
        TRANSFERENCIA = "transferencia", "Transferencia"
        TERMINAL_POS = "terminal_pos", "Terminal POS"

    class Estado(models.TextChoices):
        ACTIVA = "activa", "Activa"
        INACTIVA = "inactiva", "Inactiva"
        ARCHIVADA = "archivada", "Archivada"

    destinatario = models.ForeignKey(Destinatario, on_delete=models.PROTECT, related_name="cuentas")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    titular = models.CharField(max_length=120, blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ACTIVA)
    fecha_activacion = models.DateTimeField(null=True, blank=True)
    fecha_archivado = models.DateTimeField(null=True, blank=True)
    titular_cuit = models.CharField(max_length=11, null=True, blank=True)
    banco = models.CharField(max_length=80, blank=True)
    alias = models.CharField(max_length=60, null=True, blank=True)
    cbu = models.CharField(max_length=22, null=True, blank=True)
    tope_mensual = MontoField(null=True, blank=True)
    tope_semanal = MontoField(null=True, blank=True)
    minimo_por_pago = MontoField(null=True, blank=True)
    objetivo = MontoField(null=True, blank=True)
    proveedor = models.ForeignKey(
        Proveedor, on_delete=models.PROTECT, null=True, blank=True, related_name="cuentas"
    )
    posicion = models.PositiveIntegerField(null=True, blank=True)
    acumula_desde = models.DateTimeField("acumula desde", null=True, blank=True)

    objects = CuentaQuerySet.as_manager()

    class Meta:
        constraints = [
            _check(
                _montos_ok(), "cuenta_montos_no_negativos", "Los montos no pueden ser negativos."
            ),
            _check(
                _si(
                    _TRANSF,
                    _no_nulos("tope_mensual", "tope_semanal", "minimo_por_pago")
                    & _nulos("proveedor", "objetivo"),
                ),
                "cuenta_transferencia_campos",
                "Faltan datos de la cuenta de transferencia.",
            ),
            _check(
                _si(_TRANSF, Q(alias__isnull=False) | Q(cbu__isnull=False)),
                "cuenta_transferencia_alias_o_cbu",
                "Cargá un alias, un CBU o CVU, o los dos.",
            ),
            _check(
                _si(_TRANSF, Q(tope_mensual__gt=0)),
                "cuenta_transferencia_tope_positivo",
                "El tope mensual debe ser mayor que $0,00.",
            ),
            _check(
                _si(_TRANSF, Q(minimo_por_pago__lte=F("tope_mensual"))),
                "cuenta_transferencia_minimo",
                "El mínimo por pago no puede superar el tope mensual.",
            ),
            _check(
                _si(_TRANSF, Q(tope_semanal=0) | Q(tope_semanal__lte=F("tope_mensual"))),
                "cuenta_transferencia_semanal_tope",
                "El tope semanal no puede superar el tope mensual.",
            ),
            _check(
                _si(_TRANSF, Q(tope_semanal=0) | Q(tope_semanal__gte=F("minimo_por_pago"))),
                "cuenta_transferencia_semanal_minimo",
                "El tope semanal no puede ser menor que el mínimo por pago.",
            ),
            _check(
                _si(
                    _POS,
                    _no_nulos("proveedor", "objetivo")
                    & _nulos("alias", "cbu", "titular_cuit", *_MONTOS[:3])
                    & Q(banco=""),
                ),
                "cuenta_pos_campos",
                "Faltan datos de la cuenta de terminal POS.",
            ),
            _check(
                (_POS & Q(acumula_desde__isnull=False)) | (~_POS & Q(acumula_desde__isnull=True)),
                "cuenta_pos_acumula_desde",
                "Falta «acumula desde» de la cuenta de terminal POS.",
            ),
            _check(
                _si(_POS, Q(objetivo__gt=0)),
                "cuenta_pos_objetivo_positivo",
                "El objetivo debe ser mayor que $0,00.",
            ),
            _check(
                _si(
                    _EFECTIVO,
                    _nulos("proveedor", "alias", "cbu", "titular_cuit", *_MONTOS) & Q(banco=""),
                ),
                "cuenta_efectivo_campos",
                "Una cuenta de efectivo no tiene topes, mínimo ni objetivo.",
            ),
            _check(
                Q(cbu__isnull=True) | Q(cbu__regex=CBU_RE),
                "cuenta_cbu_formato",
                "El CBU o CVU debe tener 22 dígitos, solo números.",
            ),
            _check(
                Q(titular_cuit__isnull=True) | Q(titular_cuit__regex=CUIT_RE),
                "cuenta_titular_cuit_formato",
                "El CUIT o CUIL debe tener 11 dígitos, solo números.",
            ),
            models.UniqueConstraint(
                Lower("alias"),
                name="cuenta_alias_unico",
                violation_error_message="Ya existe una cuenta con ese alias.",
            ),
            models.UniqueConstraint(
                fields=["cbu"],
                name="cuenta_cbu_unico",
                violation_error_message="Ya existe una cuenta con ese CBU o CVU.",
            ),
            models.UniqueConstraint(
                fields=["tipo"],
                condition=Q(tipo="efectivo", estado="activa"),
                name="cuenta_efectivo_activa_unica",
                violation_error_message="Ya hay una cuenta de efectivo activa.",
            ),
            models.UniqueConstraint(
                fields=["posicion"],
                name="cuenta_posicion_unica",
                deferrable=Deferrable.DEFERRED,
                violation_error_message="Ya hay una cuenta en esa posición.",
            ),
            _check(
                Q(posicion__isnull=True)
                | (Q(tipo="transferencia", estado="activa") & Q(posicion__gte=1)),
                "cuenta_posicion_valida",
                "Solo una cuenta de transferencia Activa tiene posición.",
            ),
        ]

    def __str__(self):
        # Un solo formato en pantallas, selectores y emails (020/FR-011). Nunca el CBU entero.
        if self.tipo == self.Tipo.TRANSFERENCIA:
            detalle = self.alias or f"CBU terminado en {(self.cbu or '')[-4:]}"
        elif self.tipo == self.Tipo.TERMINAL_POS:
            detalle = self.titular or "POS"
        else:
            detalle = "Efectivo"
        return f"{self.destinatario} — {detalle}"

    @property
    def etiqueta_selector(self):
        sufijo = {self.Estado.INACTIVA: " (Inactiva)", self.Estado.ARCHIVADA: " (Archivada)"}
        return f"{self}{sufijo.get(self.estado, '')}"

    def clean(self):
        self.alias = (self.alias or "").strip() or None
        self.cbu = self.cbu or None
        self.titular_cuit = self.titular_cuit or None
        if self.pk:
            guardada = Cuenta.objects.filter(pk=self.pk).first()
            if guardada:
                if guardada.tipo != self.tipo:
                    raise ValidationError("El tipo de cuenta no cambia después del alta.")
                if guardada.proveedor_id != self.proveedor_id:
                    raise ValidationError("El proveedor no cambia después del alta.")


class TopeVigente(Registro):
    """Historial de cálculo de los topes de una cuenta (FR-028). No genera eventos de auditoría."""

    class Tipo(models.TextChoices):
        MENSUAL = "mensual", "Mensual"
        SEMANAL = "semanal", "Semanal"

    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name="topes")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    monto = MontoField()
    desde = models.DateTimeField()

    class Meta:
        constraints = [
            _check(
                Q(monto__gte=0),
                "tope_vigente_monto_no_negativo",
                "El monto del tope no puede ser negativo.",
            ),
            _check(
                _si(Q(tipo="mensual"), Q(monto__gt=0)),
                "tope_vigente_mensual_positivo",
                "El tope mensual debe ser mayor que $0,00.",
            ),
            models.UniqueConstraint(
                fields=["cuenta", "tipo", "desde"],
                name="tope_vigente_unico",
                violation_error_message="Ya hay un tope de ese tipo desde ese momento.",
            ),
        ]
        indexes = [models.Index(fields=["cuenta", "tipo", "desde"], name="tope_vigente_idx")]
