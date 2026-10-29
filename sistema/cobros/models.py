import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from sistema.core.models import MontoField, Registro
from sistema.core.tiempo import ARG

METODOS_POS = ("debito", "credito", "qr", "link")
METODOS_TARJETA = ("debito", "credito")
POS = Q(metodo__in=METODOS_POS)
TARJETA = Q(metodo__in=METODOS_TARJETA)


def ruta_comprobante(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    ahora = timezone.localtime()
    return f"comprobantes/{ahora:%Y/%m}/{uuid.uuid4().hex}{ext}"


class Cobro(Registro):
    class MotivoCancelacion(models.TextChoices):
        DATOS_ERRONEOS = "datos_erroneos", "Datos erróneos"
        NO_PAGO = "no_pago", "El paciente no pagó"
        OTRO_MEDIO = "otro_medio", "Se cobró por otro medio"
        OTRO = "otro", "Otro"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        FINALIZADO = "finalizado", "Finalizado"
        CANCELADO = "cancelado", "Cancelado"

    class Metodo(models.TextChoices):
        EFECTIVO = "efectivo", "Efectivo"
        TRANSFERENCIA = "transferencia", "Transferencia"
        DEBITO = "debito", "Débito"
        CREDITO = "credito", "Crédito"
        QR = "qr", "QR"
        LINK = "link", "Link de pago"

    cuenta = models.ForeignKey(
        "destinatarios.Cuenta", on_delete=models.PROTECT, related_name="cobros"
    )
    monto = MontoField()
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE)
    fecha = models.DateTimeField(default=timezone.now)
    excedente = models.BooleanField(default=False)
    metodo = models.CharField(max_length=20, choices=Metodo.choices, default=Metodo.TRANSFERENCIA)
    id_paciente_dentalink = models.PositiveIntegerField(null=True, blank=True)
    nombre_paciente = models.CharField(max_length=200, blank=True)
    dni_paciente = models.CharField(max_length=20, blank=True)
    faltan_items = models.BooleanField(default=False)
    banco_emisor = models.ForeignKey(
        "dentalink.Banco", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    terminal = models.ForeignKey(
        "terminales.Terminal",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cobros",
    )
    destinatario = models.ForeignKey(
        "destinatarios.Destinatario",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    comprobante = models.FileField(upload_to=ruta_comprobante, blank=True, max_length=200)
    migrado = models.BooleanField(default=False)
    monto_neto = MontoField(null=True, blank=True)
    numero_operacion = models.CharField(max_length=30, null=True, blank=True)
    ultimos_4 = models.CharField("últimos 4 dígitos", max_length=4, null=True, blank=True)
    motivo_cancelacion = models.CharField(
        max_length=20, choices=MotivoCancelacion.choices, blank=True
    )
    detalle_cancelacion = models.TextField(blank=True)
    revisado = models.BooleanField(default=False)
    comentario_revision = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(monto__gt=0),
                name="cobro_monto_positivo",
                violation_error_message="El monto debe ser mayor que $0,00.",
            ),
            models.CheckConstraint(
                condition=Q(metodo="transferencia") | ~Q(estado="pendiente"),
                name="cobro_pendiente_solo_transferencia",
                violation_error_message="Solo un cobro por transferencia puede estar Pendiente.",
            ),
            models.CheckConstraint(
                condition=(POS & Q(terminal__isnull=False)) | (~POS & Q(terminal__isnull=True)),
                name="cobro_terminal_solo_pos",
                violation_error_message=(
                    "Un cobro por terminal POS necesita una terminal; los demás, ninguna."
                ),
            ),
            models.CheckConstraint(
                condition=(POS & Q(monto_neto__gt=0, monto_neto__lte=F("monto")))
                | (~POS & Q(monto_neto__isnull=True)),
                name="cobro_neto_pos",
                violation_error_message=(
                    "El neto debe ser mayor que $0,00 y menor o igual al bruto."
                ),
            ),
            models.CheckConstraint(
                condition=(POS & Q(numero_operacion__isnull=False) & ~Q(numero_operacion=""))
                | (~POS & Q(numero_operacion__isnull=True)),
                name="cobro_operacion_pos",
                violation_error_message="Falta el número de operación.",
            ),
            models.CheckConstraint(
                condition=(TARJETA & Q(banco_emisor__isnull=False, ultimos_4__regex=r"^[0-9]{4}$"))
                | (~TARJETA & Q(banco_emisor__isnull=True, ultimos_4__isnull=True)),
                name="cobro_datos_tarjeta",
                violation_error_message="Cargá el banco emisor y los últimos 4 dígitos de la tarjeta.",
            ),
            models.CheckConstraint(
                condition=Q(migrado=True)
                | ~(POS | Q(metodo="transferencia", estado="finalizado"))
                | ~Q(comprobante=""),
                name="cobro_comprobante_obligatorio",
                violation_error_message="Falta el comprobante.",
            ),
            models.CheckConstraint(
                condition=(Q(estado="cancelado") & ~Q(motivo_cancelacion=""))
                | (~Q(estado="cancelado") & Q(motivo_cancelacion="")),
                name="cobro_motivo_si_cancelado",
                violation_error_message="Cancelar exige un motivo.",
            ),
            models.CheckConstraint(
                condition=~Q(motivo_cancelacion="otro") | ~Q(detalle_cancelacion=""),
                name="cobro_detalle_otro",
                violation_error_message="Con el motivo «Otro», escribí el detalle.",
            ),
            models.UniqueConstraint(
                fields=["terminal", "numero_operacion"],
                condition=~Q(estado="cancelado"),
                name="cobro_operacion_unica",
                violation_error_message="Ya hay un cobro con ese número de operación en la terminal.",
            ),
        ]
        indexes = [
            models.Index(fields=["cuenta", "fecha"], name="cobro_cuenta_fecha"),
            models.Index(fields=["terminal", "fecha"], name="cobro_terminal_fecha"),
        ]

    @property
    def es_pos(self):
        return self.metodo in METODOS_POS

    def clean(self):
        super().clean()
        if self.numero_operacion is not None:
            self.numero_operacion = "".join(self.numero_operacion.split()).upper() or None
        if not self.ultimos_4:
            self.ultimos_4 = None

    def __str__(self):
        return f"Cobro {self.pk} - {self.cuenta}"


class ItemCobro(Registro):
    class EstadoEnvio(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de envío"
        ENVIADO = "enviado", "Enviado"
        ERROR = "error", "Error"
        REVISAR = "revisar", "Revisar en Dentalink"
        REQUIERE_AJUSTE = "requiere_ajuste", "Requiere ajuste manual"
        AJUSTADO = "ajustado", "Ajustado a mano"
        HISTORICO = "historico", "No se envía (histórico)"
        CANCELADO = "cancelado", "No se envía (cobro cancelado)"

    cobro = models.ForeignKey(Cobro, on_delete=models.PROTECT, related_name="items")
    numero = models.PositiveIntegerField()
    id_tratamiento = models.PositiveIntegerField(null=True, blank=True)
    id_prestacion = models.PositiveIntegerField(null=True, blank=True)
    id_profesional = models.PositiveIntegerField(null=True, blank=True)
    nombre_tratamiento = models.CharField(max_length=200, blank=True)
    nombre_prestacion = models.CharField(max_length=200, blank=True)
    nombre_profesional = models.CharField(max_length=120, blank=True)
    monto = MontoField()
    estado_envio = models.CharField(
        max_length=20, choices=EstadoEnvio.choices, default=EstadoEnvio.PENDIENTE
    )
    id_pago_dentalink = models.PositiveBigIntegerField(null=True, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    proximo_intento = models.DateTimeField(null=True, blank=True)
    en_curso_desde = models.DateTimeField(null=True, blank=True)
    ultimo_error = models.TextField(blank=True)
    saldo_a_favor = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cobro", "numero"],
                name="item_numero_unico",
                violation_error_message="El número de ítem ya existe en el cobro.",
            ),
            models.UniqueConstraint(
                fields=["cobro", "id_prestacion"],
                name="item_prestacion_unica",
                violation_error_message="El cobro ya tiene un ítem de esa prestación.",
            ),
            models.UniqueConstraint(
                fields=["cobro"],
                condition=Q(saldo_a_favor=True),
                name="item_saldo_a_favor_unico",
                violation_error_message="El cobro ya tiene un ítem «saldo a favor».",
            ),
            models.CheckConstraint(
                condition=Q(monto__gt=0),
                name="item_monto_positivo",
                violation_error_message="El monto del ítem debe ser mayor que $0,00.",
            ),
            models.CheckConstraint(
                condition=Q(intentos__lte=6),
                name="item_intentos_maximo",
                violation_error_message="Un ítem tiene como máximo 6 intentos.",
            ),
        ]
        indexes = [
            models.Index(fields=["estado_envio", "proximo_intento"], name="item_estado_envio")
        ]

    @property
    def referencia(self):
        return f"{self.cobro_id}-{self.numero}"

    def __str__(self):
        return f"Ítem {self.referencia}"


class ComprobanteCobro(Registro):
    """Comprobantes del 2.º en adelante de un cobro por transferencia (FR-052). Sin monto."""

    cobro = models.ForeignKey(Cobro, on_delete=models.PROTECT, related_name="otros_comprobantes")
    archivo = models.FileField(upload_to=ruta_comprobante, max_length=200)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(archivo=""),
                name="comprobantecobro_archivo",
                violation_error_message="Falta el comprobante.",
            ),
        ]

    def __str__(self):
        return f"Comprobante {self.pk} del cobro {self.cobro_id}"


class PagoComplementario(Registro):
    class Metodo(models.TextChoices):
        TRANSFERENCIA = "transferencia", "Transferencia"
        EFECTIVO = "efectivo", "Efectivo"

    class Estado(models.TextChoices):
        REGISTRADO = "registrado", "Registrado"
        ANULADO = "anulado", "Anulado"

    class MotivoAnulacion(models.TextChoices):
        DATOS_ERRONEOS = "datos_erroneos", "Datos erróneos"
        NO_PAGO = "no_pago", "No se pagó"
        OTRO = "otro", "Otro"

    destinatario = models.ForeignKey(
        "destinatarios.Destinatario",
        on_delete=models.PROTECT,
        related_name="pagos_complementarios",
    )
    mes = models.DateField()
    monto = MontoField()
    metodo = models.CharField(max_length=20, choices=Metodo.choices)
    cuenta = models.ForeignKey(
        "destinatarios.Cuenta",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pagos_complementarios",
    )
    comprobante = models.FileField(upload_to=ruta_comprobante, blank=True, max_length=200)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.REGISTRADO)
    motivo_anulacion = models.CharField(max_length=20, choices=MotivoAnulacion.choices, blank=True)
    detalle_anulacion = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(monto__gt=0),
                name="pago_monto_positivo",
                violation_error_message="El monto debe ser mayor que $0,00.",
            ),
            models.CheckConstraint(
                condition=(Q(metodo="transferencia") & Q(cuenta__isnull=False))
                | (Q(metodo="efectivo") & Q(cuenta__isnull=True)),
                name="pago_cuenta_solo_transferencia",
                violation_error_message=(
                    "Un pago por transferencia necesita una cuenta; en efectivo, ninguna."
                ),
            ),
            models.CheckConstraint(
                condition=Q(mes__day=1),
                name="pago_mes_dia_1",
                violation_error_message="El mes debe empezar el día 1.",
            ),
            models.CheckConstraint(
                condition=(Q(estado="anulado") & ~Q(motivo_anulacion=""))
                | (Q(estado="registrado") & Q(motivo_anulacion="")),
                name="pago_motivo_si_anulado",
                violation_error_message="Anular exige un motivo.",
            ),
            models.CheckConstraint(
                condition=~Q(motivo_anulacion="otro") | ~Q(detalle_anulacion=""),
                name="pago_detalle_otro",
                violation_error_message="Con el motivo «Otro», escribí el detalle.",
            ),
        ]
        indexes = [models.Index(fields=["cuenta", "mes"], name="pago_cuenta_mes")]

    def clean(self):
        super().clean()
        if self.cuenta_id:
            if self.cuenta.tipo != "transferencia":
                raise ValidationError({"cuenta": "La cuenta debe ser una cuenta de transferencia."})
            if self.cuenta.destinatario_id != self.destinatario_id:
                raise ValidationError({"cuenta": "La cuenta debe ser del destinatario del pago."})
        if self.mes:
            corriente = timezone.localtime(timezone.now(), ARG).date().replace(day=1)
            if self.mes > corriente:
                raise ValidationError({"mes": "El mes no puede ser posterior al mes corriente."})

    def __str__(self):
        return f"Pago complementario {self.pk} - {self.destinatario}"
