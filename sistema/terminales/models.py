from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import models
from django.db.models import F, Func, Q
from django.db.models.functions import Lower

from sistema.core.models import Registro


class Terminal(Registro):
    class Estado(models.TextChoices):
        ACTIVA = "activa", "Activa"
        INACTIVA = "inactiva", "Inactiva"
        ARCHIVADA = "archivada", "Archivada"

    nombre = models.CharField(max_length=60)
    proveedor = models.ForeignKey(
        "destinatarios.Proveedor", on_delete=models.PROTECT, related_name="terminales"
    )
    acepta_credito = models.BooleanField("acepta crédito", default=False)
    acepta_debito = models.BooleanField("acepta débito", default=False)
    acepta_qr = models.BooleanField("acepta QR", default=False)
    acepta_link = models.BooleanField("acepta link de pago", default=False)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ACTIVA)
    fecha_activacion = models.DateTimeField(null=True, blank=True)
    fecha_archivado = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                Lower("nombre"),
                name="terminal_nombre_unico",
                violation_error_message="Ya existe una terminal con ese nombre.",
            ),
            models.CheckConstraint(
                condition=Q(acepta_credito=True)
                | Q(acepta_debito=True)
                | Q(acepta_qr=True)
                | Q(acepta_link=True),
                name="terminal_algun_metodo",
                violation_error_message="Elegí al menos un método de cobro.",
            ),
        ]

    def __str__(self):
        return self.nombre

    @property
    def metodos_texto(self):
        # Orden fijo: Crédito, Débito, QR, Link de pago (022/FR-024).
        etiquetas = (
            (self.acepta_credito, "Crédito"),
            (self.acepta_debito, "Débito"),
            (self.acepta_qr, "QR"),
            (self.acepta_link, "Link de pago"),
        )
        return " · ".join(t for ok, t in etiquetas if ok)

    def acepta(self, metodo):
        return {
            "credito": self.acepta_credito,
            "debito": self.acepta_debito,
            "qr": self.acepta_qr,
            "link": self.acepta_link,
        }.get(metodo, False)

    def clean(self):
        self.nombre = (self.nombre or "").strip()
        if self.pk:
            guardada = Terminal.objects.filter(pk=self.pk).first()
            if guardada and guardada.proveedor_id != self.proveedor_id:
                raise ValidationError("El proveedor no cambia después del alta.")


class TsTzRange(Func):
    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Sesion(Registro):
    terminal = models.ForeignKey(Terminal, on_delete=models.PROTECT, related_name="sesiones")
    cuenta = models.ForeignKey(
        "destinatarios.Cuenta", on_delete=models.PROTECT, related_name="sesiones"
    )
    inicio = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)

    # Manager común: se puede borrar una sesión sin cobros (research R9).
    objects = models.Manager()

    class Meta:
        ordering = ["terminal", "inicio"]
        indexes = [models.Index(fields=["terminal", "inicio"], name="sesion_terminal_inicio")]
        constraints = [
            models.CheckConstraint(
                condition=Q(fin__isnull=True) | Q(fin__gt=F("inicio")),
                name="sesion_inicio_antes_fin",
                violation_error_message="El inicio debe ser anterior al fin.",
            ),
            ExclusionConstraint(
                name="sesion_sin_superposicion",
                expressions=[
                    (TsTzRange("inicio", "fin", RangeBoundary()), RangeOperators.OVERLAPS),
                    ("terminal", RangeOperators.EQUAL),
                ],
                violation_error_message="La sesión se superpone con otra sesión de la terminal.",
            ),
        ]

    def __str__(self):
        return f"{self.terminal} desde {self.inicio}"

    def delete(self, *args, **kwargs):
        return models.Model.delete(self, *args, **kwargs)

    @property
    def abierta(self):
        return self.fin is None

    def cubre(self, fecha):
        return self.inicio <= fecha and (self.fin is None or fecha < self.fin)

    def validate_constraints(self, exclude=None):
        # Con fin < inicio el rango de la exclusión no existe y Postgres da DataError.
        if self.fin is not None and self.inicio is not None and self.fin <= self.inicio:
            raise ValidationError({NON_FIELD_ERRORS: ["El inicio debe ser anterior al fin."]})
        super().validate_constraints(exclude)

    def clean(self):
        if self.cuenta_id and self.terminal_id:
            if self.cuenta.tipo != "terminal_pos":
                raise ValidationError({"cuenta": "La cuenta debe ser de terminal POS."})
            if self.cuenta.proveedor_id != self.terminal.proveedor_id:
                raise ValidationError(
                    {"cuenta": "El proveedor de la cuenta no es el de la terminal."}
                )
