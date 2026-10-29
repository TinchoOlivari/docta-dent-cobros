import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.timezone import localtime

from sistema.core.models import MontoField, Registro
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import CUIT_RE


# Tipos de documento del receptor en ARCA (research R10).
DOCUMENTOS = {96: "DNI", 80: "CUIT", 99: "Sin identificar"}


class Emisor(Registro):
    class CondicionIva(models.TextChoices):
        RESPONSABLE_INSCRIPTO = "responsable_inscripto", "Responsable inscripto"
        MONOTRIBUTO = "monotributo", "Monotributo"
        EXENTO = "exento", "Exento"

    class TipoComprobante(models.TextChoices):
        A = "a", "Factura A"
        B = "b", "Factura B"
        C = "c", "Factura C"

    class Alicuota(models.TextChoices):
        CERO = "0", "0 %"
        DIEZ_CINCO = "10.5", "10,5 %"
        VEINTIUNO = "21", "21 %"
        VEINTISIETE = "27", "27 %"

    class Concepto(models.TextChoices):
        PRODUCTOS = "productos", "Productos"
        SERVICIOS = "servicios", "Servicios"
        PRODUCTOS_Y_SERVICIOS = "productos_y_servicios", "Productos y servicios"

    class Ambiente(models.TextChoices):
        PRUEBA = "prueba", "Prueba"
        PRODUCCION = "produccion", "Producción"

    class Estado(models.TextChoices):
        ACTIVO = "activo", "Activo"
        INACTIVO = "inactivo", "Inactivo"

    # Códigos de ARCA (research R10).
    ALICUOTA_ID = {"0": 3, "10.5": 4, "21": 5, "27": 6}

    cuit = models.CharField(max_length=11)
    razon_social = models.CharField(max_length=200)
    condicion_iva = models.CharField(max_length=30, choices=CondicionIva.choices)
    punto_venta = models.PositiveIntegerField()
    tipo_comprobante = models.CharField(max_length=1, choices=TipoComprobante.choices)
    alicuota_iva = models.CharField(max_length=5, choices=Alicuota.choices)
    concepto = models.CharField(max_length=30, choices=Concepto.choices, default=Concepto.SERVICIOS)
    descripcion = models.CharField(max_length=200, default="Servicios odontológicos")
    certificado = models.TextField(blank=True)
    clave = models.TextField(blank=True)
    certificado_vence = models.DateTimeField(null=True, blank=True)
    aviso_certificado = models.DateTimeField(null=True, blank=True)
    ambiente = models.CharField(max_length=10, choices=Ambiente.choices, default=Ambiente.PRUEBA)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ACTIVO)
    fecha_activacion = models.DateTimeField(null=True, blank=True)
    fecha_archivado = models.DateTimeField(null=True, blank=True)
    ta_token = models.TextField(blank=True)
    ta_sign = models.TextField(blank=True)
    ta_vence = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(cuit__regex=CUIT_RE),
                name="emisor_cuit_formato",
                violation_error_message="El CUIT debe tener 11 dígitos, solo números.",
            ),
            models.UniqueConstraint(
                fields=["cuit"],
                name="emisor_cuit_unico",
                violation_error_message="Ya existe un emisor con ese CUIT.",
            ),
            models.CheckConstraint(
                condition=Q(punto_venta__gte=1, punto_venta__lte=99999),
                name="emisor_punto_venta_rango",
                violation_error_message="El punto de venta va de 1 a 99999.",
            ),
        ]

    def __str__(self):
        return f"{self.razon_social} ({self.cuit})"

    def vence_pronto(self, ahora=None):
        """Faltan 30 días o menos (fecha ARG). Sin certificado: no."""
        if self.certificado_vence is None:
            return False
        vence = localtime(self.certificado_vence, ARG).date()
        return vence - localtime(ahora or timezone.now(), ARG).date() <= timedelta(days=30)

    @property
    def por_vencer(self):
        return self.vence_pronto()

    @property
    def codigo_factura(self):
        return {"a": 1, "b": 6, "c": 11}[self.tipo_comprobante]

    @property
    def codigo_nota_credito(self):
        return {"a": 3, "b": 8, "c": 13}[self.tipo_comprobante]


class ComprobanteFiscal(Registro):
    class Tipo(models.TextChoices):
        FACTURA = "factura", "Factura"
        NOTA_CREDITO = "nota_credito", "Nota de crédito"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        EMITIDO = "emitido", "Emitido"
        ERROR = "error", "Error"
        DESCARTADO = "descartado", "Descartado"

    cobro = models.ForeignKey(
        "cobros.Cobro", on_delete=models.PROTECT, related_name="comprobantes_fiscales"
    )
    emisor = models.ForeignKey(Emisor, on_delete=models.PROTECT, related_name="comprobantes")
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    asociado = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="notas_credito"
    )
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    vigente = models.BooleanField(default=True)
    a_revisar = models.BooleanField(default=False)
    cbte_tipo = models.PositiveSmallIntegerField(null=True, blank=True)
    punto_venta = models.PositiveIntegerField(null=True, blank=True)
    numero = models.PositiveBigIntegerField(null=True, blank=True)
    numero_intentado = models.PositiveBigIntegerField(null=True, blank=True)
    fecha = models.DateField(null=True, blank=True)
    concepto = models.CharField(max_length=30, choices=Emisor.Concepto.choices)
    descripcion = models.CharField(max_length=200)
    receptor_nombre = models.CharField(max_length=200, blank=True)
    doc_tipo = models.PositiveSmallIntegerField(null=True, blank=True)
    doc_numero = models.CharField(max_length=11, blank=True)
    servicio_desde = models.DateField(null=True, blank=True)
    servicio_hasta = models.DateField(null=True, blank=True)
    neto = MontoField(null=True, blank=True)
    iva = MontoField(null=True, blank=True)
    total = MontoField(null=True, blank=True)
    cae = models.CharField(max_length=14, blank=True)
    cae_vence = models.DateField(null=True, blank=True)
    motivo = models.TextField(blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    proximo_intento = models.DateTimeField(null=True, blank=True)
    en_curso_desde = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["estado", "proximo_intento"], name="arca_estado_proximo")]
        constraints = [
            models.UniqueConstraint(
                fields=["cobro"],
                condition=Q(tipo="factura", vigente=True),
                name="comprobante_factura_vigente_unica",
                violation_error_message="El cobro ya tiene una factura vigente.",
            ),
            models.UniqueConstraint(
                fields=["emisor", "punto_venta", "cbte_tipo", "numero"],
                condition=Q(numero__isnull=False),
                name="comprobante_numero_unico",
                violation_error_message="Ese número de comprobante fiscal ya existe.",
            ),
            models.UniqueConstraint(
                fields=["asociado"],
                condition=Q(tipo="nota_credito") & ~Q(estado="descartado"),
                name="comprobante_nota_credito_unica",
                violation_error_message="La factura ya tiene una nota de crédito.",
            ),
            models.CheckConstraint(
                condition=Q(tipo="nota_credito", asociado__isnull=False)
                | Q(tipo="factura", asociado__isnull=True),
                name="comprobante_asociado_segun_tipo",
                violation_error_message=(
                    "La nota de crédito lleva una factura asociada y la factura no."
                ),
            ),
            models.CheckConstraint(
                condition=~Q(estado="emitido")
                | (
                    Q(numero__isnull=False, cae_vence__isnull=False)
                    & Q(fecha__isnull=False, total__isnull=False)
                    & ~Q(cae="")
                ),
                name="comprobante_emitido_completo",
                violation_error_message=(
                    "Un comprobante emitido lleva número, CAE, vencimiento del CAE, fecha y total."
                ),
            ),
            models.CheckConstraint(
                condition=~Q(estado="error") | ~Q(motivo=""),
                name="comprobante_error_con_motivo",
                violation_error_message="Un comprobante con error lleva el motivo.",
            ),
            models.CheckConstraint(
                condition=Q(intentos__lte=6),
                name="comprobante_intentos_max",
                violation_error_message="Un comprobante no pasa de 6 intentos.",
            ),
            models.CheckConstraint(
                condition=Q(servicio_desde__isnull=True)
                | Q(servicio_hasta__isnull=True)
                | Q(servicio_desde__lte=F("servicio_hasta")),
                name="comprobante_servicio_orden",
                violation_error_message="El inicio del servicio no puede ser posterior al fin.",
            ),
            models.CheckConstraint(
                condition=~Q(estado="emitido") | Q(neto=F("total") - F("iva")),
                name="comprobante_emitido_suma",
                violation_error_message="El neto más el IVA debe ser igual al total.",
            ),
        ]

    def clean(self):
        """El documento del receptor se valida antes de emitir (FR-020)."""
        errores = {}
        numero = self.doc_numero
        if self.doc_tipo is None:
            if numero:
                errores["doc_tipo"] = "Elegí el tipo de documento."
        elif self.doc_tipo not in DOCUMENTOS:
            errores["doc_tipo"] = "El tipo de documento no es válido."
        elif numero and not re.fullmatch(r"[0-9]+", numero):
            errores["doc_numero"] = "El número de documento lleva solo dígitos."
        elif self.doc_tipo == 80 and len(numero) != 11:
            errores["doc_numero"] = "El CUIT lleva 11 dígitos."
        elif self.doc_tipo == 96 and not numero.strip("0"):
            errores["doc_numero"] = "Escribí el número de DNI."
        elif self.doc_tipo == 99 and numero not in ("", "0"):
            errores["doc_numero"] = "Sin identificar no lleva número de documento."
        if errores:
            raise ValidationError(errores)

    def __str__(self):
        return f"{self.get_tipo_display()} del cobro {self.cobro_id} ({self.get_estado_display()})"

    @property
    def en_curso(self):
        return self.en_curso_desde is not None
