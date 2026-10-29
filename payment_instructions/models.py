import os
import uuid
from zoneinfo import ZoneInfo
from django.db import models, transaction
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils import timezone
from django.core.exceptions import ValidationError
from .utils.utils import format_money_thousand_separator
from django.utils.translation import gettext_lazy as _

argentina_tz = ZoneInfo("America/Argentina/Buenos_Aires")


def get_now_arg():
    """Get current time in Argentina timezone"""
    return timezone.now().astimezone(argentina_tz)


def modify_file_name(instance, filename):
    # Folder structure
    now = timezone.now()
    year = now.strftime("%Y")
    month = now.strftime("%m")

    # Original extension including dot
    base, ext = os.path.splitext(filename)

    # Payment.id if available; fallback to 'tmp' until instance has pk
    payment_id = instance.pk if instance.pk is not None else "tmp"

    # Short uuid for safety (first 8 digits)
    random_uuid = uuid.uuid4().hex[:8]

    new_filename = f"{payment_id}_{random_uuid}{ext}"
    return os.path.join(f"comprobantes/{year}/{month}", new_filename)


class UserManager(BaseUserManager):
    def create_user(self, username, email=None, password=None, **extra_fields):
        if not username:
            raise ValueError("Username is required")
        email = self.normalize_email(email) if email else None
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.ADMINISTRATOR)
        return self.create_user(username, email, password, **extra_fields)


class User(AbstractUser):
    ADMINISTRATOR = "administrator"
    OPERATOR = "operator"
    PROFESSIONAL = "professional"

    ROLE_CHOICES = [
        (ADMINISTRATOR, "Administrator"),
        (OPERATOR, "Operator"),
        (PROFESSIONAL, "Professional"),
    ]

    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default=OPERATOR, help_text="Rol del usuario"
    )

    objects = UserManager()

    def is_administrator(self):
        return self.role == self.ADMINISTRATOR

    def is_operator(self):
        return self.role == self.OPERATOR

    def is_professional(self):
        return self.role == self.PROFESSIONAL

    def __str__(self):
        return f"{self.username}"

    def get_current_month_amount(self):
        """Plata cobrada por este profesional en el mes. Solo pagos COMPLETADO: un
        pendiente todavía no entró y un cancelado nunca va a entrar."""
        if not self.is_professional():
            return 0
        # Hora Argentina, no UTC: con timezone.now() los pagos de las últimas tres horas
        # del último día del mes caen en el mes siguiente y se los cuenta al período
        # equivocado. Es un bug que solo aparece de noche a fin de mes.
        now_arg = get_now_arg()
        start_of_month = now_arg.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return (
            self.professional_payments.filter(
                created_at__gte=start_of_month, estado=Payment.COMPLETADO
            )
            .aggregate(total=models.Sum("amount"))
            .get("total")
            or 0
        )


class PaymentRecipientManager(models.Manager):
    def get_available_recipients(self, amount=None):
        """Get active recipients that can receive payments, ordered by priority
        Excludes DEPOSIT_ONLY recipients from regular payment assignment"""
        queryset = self.filter(is_active=True, recipient_type=PaymentRecipient.REGULAR)

        if amount is not None:
            # Filter recipients that can receive the specified amount
            available_recipients = []
            for recipient in queryset:
                if recipient.can_receive_amount(amount):
                    available_recipients.append(recipient.pk)
            queryset = queryset.filter(pk__in=available_recipients)

        return queryset.order_by("priority_order", "name")

    def find_best_recipient(self, amount):
        """Find the best recipient for a given amount based on priority and availability
        Excludes DEPOSIT_ONLY recipients from regular payment assignment"""
        available = self.get_available_recipients(amount)

        # First try: exact or close match that doesn't waste capacity
        # for recipient in available:
        #     remaining = recipient.get_remaining_amount()
        #     # Prefer recipients where the amount uses significant capacity but doesn't waste much
        #     if remaining >= amount and (remaining - amount) <= remaining * Decimal('0.3'):
        #         return recipient

        # Second try: any recipient that can handle the amount, by priority
        for recipient in available:
            if recipient.can_receive_amount(amount):
                return recipient

        return None


class PaymentRecipient(models.Model):
    REGULAR = "REGULAR"
    DEPOSIT_ONLY = "DEPOSIT_ONLY"

    RECIPIENT_TYPE_CHOICES = [
        (REGULAR, "Destinatario Regular"),
        (DEPOSIT_ONLY, "Solo para Señas"),
    ]

    name = models.CharField(
        verbose_name="Nombre", max_length=200, help_text="Nombre del destinatario"
    )
    alias = models.CharField(
        max_length=100,
        unique=True,
        help_text="Alias del destinatario",
        blank=True,
        null=True,
    )
    cbu = models.CharField(
        verbose_name="CBU / CVU",
        max_length=22,
        unique=True,
        blank=True,
        null=True,
        help_text="CBU / CVU del destinatario",
    )
    recipient_type = models.CharField(
        max_length=15,
        choices=RECIPIENT_TYPE_CHOICES,
        default=REGULAR,
        verbose_name="Tipo de destinatario",
        help_text="Regular: participa en asignación automática. Solo para Señas: recibe únicamente señas sin límites",
    )
    max_amount = models.PositiveIntegerField(
        verbose_name="Monto máximo",
        help_text="Monto máximo que el destinatario puede recibir por mes",
    )
    weekly_cap_amount = models.PositiveIntegerField(
        verbose_name="Monto máximo semanal",
        help_text="Monto máximo que el destinatario puede recibir por semana (0 = sin límite)",
        default=0,
    )
    min_threshold = models.PositiveIntegerField(
        verbose_name="Mínimo por pago",
        default=0,
        help_text="Monto mínimo que puede recibir por pago (0 = sin mínimo)",
    )
    priority_order = models.PositiveIntegerField(
        verbose_name="Orden",
        default=1,
        help_text="Orden de prioridad para la selección automática (Menor numero es mayor prioridad)",
    )
    is_active = models.BooleanField(
        verbose_name="Activo",
        default=True,
        help_text="Si es Verdadero, el destinatario puede recibir pagos. Si es Falso, el destinatario no puede recibir pagos",
    )
    notes = models.TextField(
        verbose_name="Observacion",
        blank=True,
        help_text="Comentario adicional sobre el destinatario",
    )
    created_at = models.DateTimeField(verbose_name="Creado", auto_now_add=True)
    updated_at = models.DateTimeField(verbose_name="Actualizado", auto_now=True)

    objects = PaymentRecipientManager()

    class Meta:
        ordering = ["priority_order", "name"]
        verbose_name = "Destinatario"
        verbose_name_plural = "Destinatarios"

    def __str__(self):
        return f"{self.name}"

    def clean(self):
        """Validate model data"""
        super().clean()

        errors = {}

        # Validate that only one DEPOSIT_ONLY recipient can be active
        if self.recipient_type == self.DEPOSIT_ONLY and self.is_active:
            existing = PaymentRecipient.objects.filter(
                recipient_type=self.DEPOSIT_ONLY, is_active=True
            ).exclude(pk=self.pk if self.pk else 0)
            if existing.exists():
                errors["recipient_type"] = (
                    "Solo puede existir un destinatario activo para señas. Ya existe: {}".format(
                        existing.first().name
                    )
                )

        if self.max_amount and self.min_threshold and self.min_threshold > self.max_amount:
            errors["min_threshold"] = "El mínimo por pago no puede superar el monto máximo."

        if (
            self.weekly_cap_amount
            and self.weekly_cap_amount != 0
            and self.weekly_cap_amount > self.max_amount
        ):
            errors["weekly_cap_amount"] = (
                "El monto máximo semanal no puede superar el monto máximo."
            )

        if (
            self.weekly_cap_amount
            and self.weekly_cap_amount != 0
            and self.weekly_cap_amount < self.min_threshold
        ):
            errors["weekly_cap_amount"] = (
                "El monto máximo semanal no puede ser menor al monto mínimo por pago."
            )

        if not self.alias and not self.cbu:
            errors["alias"] = "Debe ingresar al menos el alias."

        # Validate CBU format (should be 22 digits)
        if self.cbu:
            if not self.cbu.isdigit():
                errors["cbu"] = "El CBU debe contener solo dígitos."
            elif len(self.cbu) != 22:
                errors["cbu"] = "El CBU debe tener 22 dígitos."

        # Check for duplicate priority (but allow during save process)
        if not hasattr(self, "_skip_priority_validation"):
            existing = PaymentRecipient.objects.filter(priority_order=self.priority_order)
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                # This will be handled automatically by the save method
                pass

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Override save to handle unique priority logic and auto-set max_amount for DEPOSIT_ONLY"""

        # Auto-set max_amount to 1 for DEPOSIT_ONLY recipients
        if self.recipient_type == self.DEPOSIT_ONLY:
            self.max_amount = 1

        # Use transaction to ensure atomicity
        with transaction.atomic():
            # Check if this is an update and priority has changed
            if self.pk:
                try:
                    old_instance = PaymentRecipient.objects.get(pk=self.pk)
                    old_priority = old_instance.priority_order
                except PaymentRecipient.DoesNotExist:
                    old_priority = None
            else:
                old_priority = None

            new_priority = self.priority_order

            # If priority changed or this is a new instance, adjust other priorities
            if old_priority != new_priority:
                self._adjust_priorities(old_priority, new_priority)

            super().save(*args, **kwargs)

    def _adjust_priorities(self, old_priority, new_priority):
        """Adjust priorities of other recipients when this one changes"""
        # Get all other recipients (excluding this one if it exists)
        other_recipients = PaymentRecipient.objects.exclude(pk=self.pk if self.pk else 0)

        if old_priority is None:
            # New recipient - shift all recipients with priority >= new_priority
            other_recipients.filter(priority_order__gte=new_priority).update(
                priority_order=models.F("priority_order") + 1
            )
        else:
            # Existing recipient changing priority
            if new_priority < old_priority:
                # Moving up (lower number = higher priority)
                # Shift down recipients between new_priority and old_priority-1
                other_recipients.filter(
                    priority_order__gte=new_priority, priority_order__lt=old_priority
                ).update(priority_order=models.F("priority_order") + 1)
            elif new_priority > old_priority:
                # Moving down (higher number = lower priority)
                # Shift up recipients between old_priority+1 and new_priority
                other_recipients.filter(
                    priority_order__gt=old_priority, priority_order__lte=new_priority
                ).update(priority_order=models.F("priority_order") - 1)

    def delete(self, *args, **kwargs):
        """Override delete to maintain priority order continuity"""
        priority_to_remove = self.priority_order
        super().delete(*args, **kwargs)

        # Shift up all recipients with priority > deleted priority
        PaymentRecipient.objects.filter(priority_order__gt=priority_to_remove).update(
            priority_order=models.F("priority_order") - 1
        )

    def get_current_month_received(self, exclude_payment=None):
        """Cupo mensual consumido: cuenta pendientes y completados, no cancelados.

        Un pago PENDIENTE ocupa cupo desde que se crea — para eso se crea en el mismo
        momento en que se sugiere el destinatario. Solo cancelarlo lo libera.

        Ojo: esto responde "cuánto cupo queda", no "cuánta plata entró". Para lo
        segundo hay que contar solo COMPLETADO (ver get_monthly_totals)."""
        now_arg = get_now_arg()
        start_of_month_arg = now_arg.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_of_month_utc = start_of_month_arg.astimezone(ZoneInfo("UTC"))
        queryset = self.payments.filter(created_at__gte=start_of_month_utc).exclude(
            estado=Payment.CANCELADO
        )

        # Exclude deposit payments from limits calculation (only for REGULAR recipients)
        if self.recipient_type == self.REGULAR:
            queryset = queryset.filter(payment_type=Payment.REGULAR)

        # Exclude specific payment if provided (useful when editing)
        if exclude_payment:
            queryset = queryset.exclude(pk=exclude_payment.pk)

        total = queryset.aggregate(total=models.Sum("amount"))["total"]
        return total or 0

    def get_current_week_received(self, exclude_payment=None):
        """Cupo semanal consumido (lunes 00:00 a domingo 23:59:59, hora Argentina).

        Igual que el mensual: cuenta pendientes y completados, no cancelados."""
        # Monday is 0 and Sunday is 6
        now_arg = get_now_arg()
        start_of_week = (now_arg - timezone.timedelta(days=now_arg.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        end_of_week = start_of_week + timezone.timedelta(days=6, hours=23, minutes=59, seconds=59)

        queryset = (
            self.payments.filter(created_at__gte=start_of_week)
            .filter(created_at__lte=end_of_week)
            .exclude(estado=Payment.CANCELADO)
        )

        # Exclude deposit payments from limits calculation (only for REGULAR recipients)
        if self.recipient_type == self.REGULAR:
            queryset = queryset.filter(payment_type=Payment.REGULAR)

        if exclude_payment:
            queryset = queryset.exclude(pk=exclude_payment.pk)
        total = queryset.aggregate(total=models.Sum("amount"))["total"]
        return total or 0

    def get_remaining_amount(self, exclude_payment=None):
        """Get remaining amount available for this recipient this month"""
        return self.max_amount - self.get_current_month_received(exclude_payment=exclude_payment)

    def get_remaining_weekly_amount(self, exclude_payment=None):
        """Get remaining amount available for this recipient this week.
        Returns None if weekly cap is disabled (0)."""
        if not self.weekly_cap_amount or self.weekly_cap_amount <= 0:
            return None
        return self.weekly_cap_amount - self.get_current_week_received(
            exclude_payment=exclude_payment
        )

    def get_received_per_week_of_month(self, month=None, year=None):
        """Get received amount per week of the month"""
        now = get_now_arg()
        if not month:
            month = now.month
        if not year:
            year = now.year

        # Get first day of the month
        first_day = timezone.datetime(year, month, 1).replace(tzinfo=now.tzinfo)

        # Calculate last day of the month
        if month == 12:
            last_day = timezone.datetime(year + 1, 1, 1).replace(
                tzinfo=now.tzinfo
            ) - timezone.timedelta(days=1)
        else:
            last_day = timezone.datetime(year, month + 1, 1).replace(
                tzinfo=now.tzinfo
            ) - timezone.timedelta(days=1)

        # return a list for each week of the month (up to 6 weeks to cover all possible cases)
        weeks = []
        current_date = first_day

        while current_date <= last_day:
            # Calculate start and end of current week (Monday to Sunday)
            week_start = current_date - timezone.timedelta(days=current_date.weekday())
            week_end = week_start + timezone.timedelta(days=6)

            # Ensure we don't go beyond the month boundaries
            week_start = max(week_start, first_day)
            week_end = min(
                week_end, last_day.replace(hour=23, minute=59, second=59, microsecond=999999)
            )

            # Cupo consumido en la semana, igual que get_current_month_received y
            # get_current_week_received: cuenta pendientes, excluye cancelados. Va con
            # ellos en la ficha del destinatario, y si contara otra cosa el total del
            # mes y la suma de las semanas no cerrarían.
            week_total = (
                self.payments.filter(created_at__gte=week_start, created_at__lte=week_end)
                .exclude(estado=Payment.CANCELADO)
                .aggregate(total=models.Sum("amount"))["total"]
                or 0
            )

            weeks.append(week_total)

            # Move to next week
            current_date = week_end + timezone.timedelta(days=1)

            # Safety check to avoid infinite loop
            if len(weeks) >= 6:
                break

        return weeks

    def is_completed_this_month(self):
        """Check if recipient has reached max amount this month"""
        return self.get_remaining_amount() <= 0

    def can_receive_amount(self, amount, exclude_payment=None):
        """Check if recipient can receive the specified amount"""
        if not self.is_active:
            return False

        # DEPOSIT_ONLY recipients have no limits
        if self.recipient_type == self.DEPOSIT_ONLY:
            return True

        # Respect minimum threshold amount per payment
        if amount < (self.min_threshold or 0):
            return False

        # Check monthly limit
        if self.get_remaining_amount(exclude_payment=exclude_payment) < amount:
            return False

        # return self.get_remaining_amount(exclude_payment=exclude_payment) >= amount and amount >= (self.min_threshold or 0)

        # Check weekly limit if enabled (> 0)
        remaining_week = self.get_remaining_weekly_amount(exclude_payment=exclude_payment)

        if remaining_week is not None and remaining_week < amount:
            return False

        return True

    def get_capacity_percentage(self):
        """Get the percentage of capacity used this month"""
        if self.max_amount == 0:
            return 100
        received = self.get_current_month_received()
        return float((received / self.max_amount) * 100)

    def get_status(self):
        """Get current status of the recipient"""
        if not self.is_active:
            return "inactive"

        return "available"

    def suggest_max_payment(self):
        """Suggest maximum payment amount that can be made to this recipient"""
        if not self.is_active:
            return 0

        return self.get_remaining_amount()

    @classmethod
    def get_payment_summary(cls):
        """Get summary of all recipients and their current status"""
        summary = {
            "total_recipients": cls.objects.count(),
            "active_recipients": cls.objects.filter(is_active=True).count(),
            "completed_this_month": 0,
            "available_recipients": 0,
            "total_capacity": 0,
            "total_used": 0,
            "recipients_by_status": {},
        }

        status_counts = {}
        for recipient in cls.objects.filter(is_active=True):
            status = recipient.get_status()
            status_counts[status] = status_counts.get(status, 0) + 1

            if status in ["completed_monthly", "completed_onetime"]:
                summary["completed_this_month"] += 1
            elif status in ["available", "half_full", "nearly_full", "available_onetime"]:
                summary["available_recipients"] += 1

        summary["recipients_by_status"] = status_counts
        return summary

    @classmethod
    def get_deposit_recipient(cls):
        """Get the unique active deposit-only recipient"""
        return cls.objects.filter(recipient_type=cls.DEPOSIT_ONLY, is_active=True).first()


class PaymentManager(models.Manager):
    def crear_pendiente(
        self,
        payment_recipient,
        amount,
        operator_user,
        patient_name,
        professional,
        observaciones=None,
    ):
        """Crea una transferencia PENDIENTE tomando el cupo bajo lock, en la misma
        transacción en la que se decide el destinatario.

        Entre "el sistema sugiere esta cuenta" y "esta cuenta queda ocupada" no pasa
        nada: son la misma operación. Dos operadores que buscan a la vez se ponen en
        fila sobre el lock del destinatario, y el segundo revalida contra el cupo que
        el primero ya consumió.

        Devuelve None si el destinatario no puede recibir el monto. Quien llama decide
        qué hacer con eso: buscar el siguiente por prioridad, o avisar que no hay."""
        with transaction.atomic():
            recipient_locked = PaymentRecipient.objects.select_for_update().get(
                pk=payment_recipient.pk
            )
            if not recipient_locked.can_receive_amount(amount):
                return None
            return self.create(
                amount=amount,
                payment_type=Payment.REGULAR,
                metodo=Payment.TRANSFERENCIA,
                estado=Payment.PENDIENTE,
                payment_recipient=recipient_locked,
                patient_name=patient_name,
                professional=professional,
                operator_user=operator_user,
                observaciones=observaciones,
            )


class Payment(models.Model):
    REGULAR = "REGULAR"
    DEPOSIT = "DEPOSIT"

    PAYMENT_TYPE_CHOICES = [
        (REGULAR, "Pago Regular"),
        (DEPOSIT, "Seña"),
    ]

    TRANSFERENCIA = "TRANSFERENCIA"
    EFECTIVO = "EFECTIVO"
    POSNET_QR = "POSNET_QR"
    POSNET_TARJETA = "POSNET_TARJETA"
    LINK_PAGO = "LINK_PAGO"

    # POSNET_QR, POSNET_TARJETA y LINK_PAGO están acá para no volver a tocar este campo
    # cuando lleguen (Fase 3) — todavía no tienen flujo de creación ni se pueden elegir
    # desde ningún form. Solo TRANSFERENCIA y EFECTIVO están implementados.
    METODO_CHOICES = [
        (TRANSFERENCIA, "Transferencia"),
        (EFECTIVO, "Efectivo"),
        (POSNET_QR, "Posnet QR"),
        (POSNET_TARJETA, "Posnet Tarjeta"),
        (LINK_PAGO, "Link de pago"),
    ]

    # Métodos con destinatario bancario y chequeo de tope. El resto (por ahora,
    # efectivo) no tiene cupo que cuidar.
    METODOS_CON_DESTINATARIO = (TRANSFERENCIA,)

    # Métodos que esperan un comprobante para darse por cobrados. El resto nace
    # COMPLETADO: en efectivo la plata está en la mano, en tarjeta la aprobación
    # del posnet es el comprobante, y en QR el comprobante es opcional y se sube
    # (si se sube) en la misma carga, nunca después.
    METODOS_CON_COMPROBANTE = (TRANSFERENCIA, LINK_PAGO)

    PENDIENTE = "PENDIENTE"
    COMPLETADO = "COMPLETADO"
    CANCELADO = "CANCELADO"

    ESTADO_CHOICES = [
        (PENDIENTE, "Pendiente"),
        (COMPLETADO, "Completado"),
        (CANCELADO, "Cancelado"),
    ]

    PACIENTE_NO_PAGO = "PACIENTE_NO_PAGO"
    COBRADO_OTRO_MEDIO = "COBRADO_OTRO_MEDIO"
    ERROR_DE_CARGA = "ERROR_DE_CARGA"
    OTRO = "OTRO"

    MOTIVO_CANCELACION_CHOICES = [
        (ERROR_DE_CARGA, "Datos erróneos"),
        (PACIENTE_NO_PAGO, "El paciente no pagó"),
        (COBRADO_OTRO_MEDIO, "Se cobró por otro medio"),
        (OTRO, "Otro"),
    ]

    amount = models.PositiveIntegerField(verbose_name="Monto", help_text="Monto del pago")
    payment_type = models.CharField(
        max_length=10,
        choices=PAYMENT_TYPE_CHOICES,
        default=REGULAR,
        verbose_name="Tipo de pago",
        help_text="Regular: pago normal. Seña: anticipo.",
    )
    metodo = models.CharField(
        max_length=15,
        choices=METODO_CHOICES,
        default=TRANSFERENCIA,
        verbose_name="Método",
        help_text="Cómo se cobró: transferencia, efectivo, etc.",
    )
    payment_recipient = models.ForeignKey(
        PaymentRecipient,
        verbose_name="Destinatario",
        on_delete=models.PROTECT,
        related_name="payments",
        help_text="Destinatario del pago. Solo aplica a métodos con cuenta receptora.",
        null=True,
        blank=True,
    )
    professional = models.ForeignKey(
        User,
        verbose_name="Profesional",
        on_delete=models.PROTECT,
        related_name="professional_payments",
        help_text="Profesional asociado al pago",
        null=False,
        blank=False,
        limit_choices_to={"role": User.PROFESSIONAL},
    )
    patient_name = models.CharField(
        verbose_name="Paciente", max_length=200, help_text="Nombre del paciente"
    )
    proof_of_payment_file = models.FileField(
        verbose_name="Comprobante",
        upload_to=modify_file_name,
        help_text="Subir comprobante de pago (imagen o PDF)",
        blank=True,
    )
    operator_user = models.ForeignKey(
        User,
        verbose_name="Operador",
        on_delete=models.PROTECT,
        related_name="registered_payments",
        help_text="Operador que registró este pago",
    )
    observaciones = models.TextField(
        verbose_name="Observaciones",
        blank=True,
        null=True,
        help_text="Observaciones adicionales sobre el pago",
    )
    created_at = models.DateTimeField(verbose_name="Creado", default=timezone.now)
    reviewed = models.BooleanField(
        default=False,
        verbose_name="Revisado",
        help_text="Indica si este pago ya fue revisado en el administrador.",
    )

    estado = models.CharField(
        max_length=10,
        choices=ESTADO_CHOICES,
        default=COMPLETADO,
        verbose_name="Estado",
        help_text=(
            "Pendiente: falta el comprobante, pero ya tomó cupo del destinatario. "
            "Completado: cobrado. Cancelado: no se cobró y liberó el cupo."
        ),
    )
    cancelado_at = models.DateTimeField(
        verbose_name="Cancelado el", null=True, blank=True, editable=False
    )
    cancelado_por = models.ForeignKey(
        User,
        verbose_name="Cancelado por",
        on_delete=models.PROTECT,
        related_name="cancelled_payments",
        null=True,
        blank=True,
        editable=False,
    )
    motivo_cancelacion = models.CharField(
        max_length=20,
        choices=MOTIVO_CANCELACION_CHOICES,
        verbose_name="Motivo de cancelación",
        blank=True,
        null=True,
    )
    observacion_cancelacion = models.TextField(
        verbose_name="Detalle de la cancelación",
        blank=True,
        null=True,
        help_text='Obligatorio cuando el motivo es "Otro".',
    )

    objects = PaymentManager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Pago"
        verbose_name_plural = "Pagos"
        indexes = [
            models.Index(fields=["estado", "created_at"]),
        ]
        permissions = [
            (
                "view_all_deposit_payments_in_admin",
                "Can view all señas, not only own ones.",
            ),
            (
                "mark_payment_reviewed",
                "Puede marcar pagos como revisados",
            ),
            (
                "view_all_payment_history_in_admin",
                "Puede ver todo el historial de pagos en admin",
            ),
        ]

    def __str__(self):
        destino = (
            self.payment_recipient.alias if self.payment_recipient_id else self.get_metodo_display()
        )
        return f"${format_money_thousand_separator(self.amount)} to {destino} on {self.created_at.strftime('%Y-%m-%d')}"

    @property
    def amount_display(self):
        return format_money_thousand_separator(self.amount)

    def completar(self, proof_file, observaciones=None):
        """Adjunta el comprobante y da el pago por cobrado.

        No revalida el tope: el cupo se tomó cuando se creó el pendiente, y para
        entonces el dinero ya se movió en la vida real. Un monto distinto al que se
        buscó no se edita acá — se cancela este pago y se carga uno nuevo.

        Las observaciones sí se pueden tocar mientras el pago está pendiente: no
        afectan cupo ni monto, así que no hace falta cancelar para corregirlas."""
        if self.estado != self.PENDIENTE:
            raise ValidationError(
                {"estado": f"Solo un pago pendiente puede completarse (está {self.estado})."}
            )
        if not proof_file:
            raise ValidationError({"proof_of_payment_file": "El comprobante es requerido."})
        self.proof_of_payment_file = proof_file
        self.estado = self.COMPLETADO
        update_fields = ["proof_of_payment_file", "estado"]
        if observaciones is not None:
            self.observaciones = observaciones
            update_fields.append("observaciones")
        self.save(update_fields=update_fields)

    def cancelar(self, motivo, usuario, observacion=None):
        """Marca el pago como cancelado y libera el cupo que ocupaba.

        Nunca borra: es plata, tiene que quedar el rastro de quién canceló y por qué.
        Un cancelado no vuelve atrás — si el pago sí ocurrió, se carga uno nuevo."""
        if self.estado == self.CANCELADO:
            raise ValidationError({"estado": "Este pago ya está cancelado."})
        if motivo not in dict(self.MOTIVO_CANCELACION_CHOICES):
            raise ValidationError({"motivo_cancelacion": "Motivo de cancelación inválido."})
        observacion = (observacion or "").strip() or None
        if motivo == self.OTRO and not observacion:
            raise ValidationError(
                {"observacion_cancelacion": 'Detalle el motivo cuando elige "Otro".'}
            )

        self.estado = self.CANCELADO
        self.motivo_cancelacion = motivo
        self.observacion_cancelacion = observacion
        self.cancelado_por = usuario
        self.cancelado_at = timezone.now()
        self.save(
            update_fields=[
                "estado",
                "motivo_cancelacion",
                "observacion_cancelacion",
                "cancelado_por",
                "cancelado_at",
            ]
        )

    def _validar_cancelacion(self):
        """Un pago cancelado siempre tiene motivo, y "Otro" exige el detalle escrito.

        Corre también bajo _admin_bypass_validation: sin motivo, el registro no dice
        por qué se soltó ese cupo, y eso no se reconstruye después."""
        if self.estado != self.CANCELADO:
            return
        if not self.motivo_cancelacion:
            raise ValidationError({"motivo_cancelacion": "Un pago cancelado necesita un motivo."})
        if (
            self.motivo_cancelacion == self.OTRO
            and not (self.observacion_cancelacion or "").strip()
        ):
            raise ValidationError(
                {"observacion_cancelacion": 'Detalle el motivo cuando elige "Otro".'}
            )

    def clean(self):
        """Validate payment data"""
        super().clean()

        # Validate patient name: not empty/blank and at least 3 letters
        name = (self.patient_name or "").strip()
        if not name:
            raise ValidationError({"patient_name": "Nombre del paciente es requerido."})
        letter_count = sum(1 for c in name if c.isalpha())
        if letter_count < 3:
            raise ValidationError(
                {"patient_name": "El nombre del paciente debe tener al menos 3 letras."}
            )

        # Validate professional role
        if self.professional_id and not self.professional.is_professional():
            raise ValidationError(
                {
                    "professional": "Solo usuarios con rol de profesional pueden ser asignados a pagos."
                }
            )

        self._validar_cancelacion()

        # Solo los métodos que esperan comprobante pueden quedar pendientes. En efectivo
        # la plata está en la mano; no hay nada que esperar.
        if self.estado == self.PENDIENTE and self.metodo not in self.METODOS_CON_COMPROBANTE:
            raise ValidationError(
                {"estado": f"Un pago por {self.get_metodo_display()} no puede quedar pendiente."}
            )

        # Validate metodo <-> destinatario consistency
        if self.metodo in self.METODOS_CON_DESTINATARIO:
            if not self.payment_recipient_id:
                raise ValidationError(
                    {"payment_recipient": "Este método de pago requiere un destinatario."}
                )
        elif self.payment_recipient_id:
            raise ValidationError(
                {"payment_recipient": "Este método de pago no debe tener un destinatario asignado."}
            )

        # Validate payment_type and recipient_type match
        if self.payment_recipient_id:
            if self.payment_type == self.DEPOSIT:
                # DEPOSIT payments must use DEPOSIT_ONLY recipients
                if self.payment_recipient.recipient_type != PaymentRecipient.DEPOSIT_ONLY:
                    raise ValidationError(
                        {
                            "payment_recipient": 'Las señas solo pueden asignarse al destinatario de señas. Seleccione un destinatario de tipo "Solo para Señas".'
                        }
                    )
            else:
                # REGULAR payments must use REGULAR recipients
                if self.payment_recipient.recipient_type != PaymentRecipient.REGULAR:
                    raise ValidationError(
                        {
                            "payment_recipient": "Los pagos regulares no pueden asignarse al destinatario de señas. Seleccione un destinatario regular."
                        }
                    )

        if getattr(self, "_admin_bypass_validation", False):
            # El administrador corrige o carga datos a mano desde el admin de Django.
            # Solo el tope (mensual/semanal) queda afuera del chequeo: el resto de la
            # coherencia estructural de arriba (nombre, profesional, método↔destinatario,
            # tipo de pago↔tipo de destinatario, motivo de cancelación) se valida igual,
            # admin incluido. Un pago con datos rotos no se puede reconstruir después.
            return

        # Skip amount validations for DEPOSIT payments
        if self.payment_type == self.DEPOSIT:
            return

        # Un pago cancelado no consume cupo: no tiene sentido validarlo contra el tope.
        if self.estado == self.CANCELADO:
            return

        if self.payment_recipient_id and self.amount:
            # When editing existing payment, exclude current payment from capacity calculation
            exclude_payment = self if self.pk else None

            if not self.payment_recipient.can_receive_amount(
                self.amount, exclude_payment=exclude_payment
            ):
                remaining_month = self.payment_recipient.get_remaining_amount(
                    exclude_payment=exclude_payment
                )
                remaining_week = self.payment_recipient.get_remaining_weekly_amount(
                    exclude_payment=exclude_payment
                )

                # Weekly cap reached or exceeded
                if remaining_week is not None:
                    if remaining_week <= 0:
                        raise ValidationError(
                            {
                                "payment_recipient": f"{self.payment_recipient.alias} ya alcanzó su límite semanal."
                            }
                        )
                    if remaining_week < self.amount:
                        raise ValidationError(
                            {
                                "amount": f"El monto excede el saldo semanal restante. Disponible: ${format_money_thousand_separator(remaining_week)}"
                            }
                        )

                # Monthly cap checks
                if remaining_month <= 0:
                    raise ValidationError(
                        {
                            "payment_recipient": f"{self.payment_recipient.alias} ya alcanzo su limite mensual."
                        }
                    )
                elif self.amount < self.payment_recipient.min_threshold:
                    raise ValidationError(
                        {
                            "amount": f"El monto es menor al monto mínimo por pago. Mínimo: ${format_money_thousand_separator(self.payment_recipient.min_threshold)}"
                        }
                    )
                else:
                    raise ValidationError(
                        {
                            "amount": f"El monto excede el saldo restante. Disponible: ${format_money_thousand_separator(remaining_month)}"
                        }
                    )

    @classmethod
    def suggest_recipient_for_amount(cls, amount):
        """Suggest the best recipient for a given payment amount"""
        return PaymentRecipient.objects.find_best_recipient(amount)

    def delete(self, *args, **kwargs):
        """Override delete to remove proof_of_payment_file from filesystem"""
        if self.proof_of_payment_file:
            # Delete the file from filesystem
            if os.path.isfile(self.proof_of_payment_file.path):
                os.remove(self.proof_of_payment_file.path)
        super().delete(*args, **kwargs)

    @classmethod
    def get_monthly_totals(cls, year=None, month=None):
        """Plata cobrada en el mes. Solo COMPLETADO: esto responde "cuánto entró",
        no "cuánto cupo se consumió" (para eso, get_current_month_received)."""
        now = get_now_arg()
        year = year or now.year
        month = month or now.month

        payments = cls.objects.filter(
            created_at__year=year, created_at__month=month, estado=cls.COMPLETADO
        )

        return {
            "total_amount": payments.aggregate(total=models.Sum("amount"))["total"] or 0,
            "payment_count": payments.count(),
            "unique_recipients": payments.values("payment_recipient").distinct().count(),
            "unique_operators": payments.values("operator_user").distinct().count(),
        }


class Banco(models.Model):
    """Catálogo de bancos emisores de tarjeta, para el form de pago con tarjeta."""

    nombre = models.CharField(
        verbose_name="Nombre", max_length=100, unique=True, help_text="Nombre del banco emisor"
    )
    logo = models.ImageField(
        verbose_name="Logo",
        upload_to="bancos/",
        blank=True,
        null=True,
        help_text="Logo del banco (opcional)",
    )
    is_active = models.BooleanField(
        verbose_name="Activo",
        default=True,
        help_text="Si es falso, no aparece para elegir en pagos nuevos.",
    )

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Banco"
        verbose_name_plural = "Bancos"

    def __str__(self):
        return self.nombre


class CuentaPosnet(models.Model):
    """Una cuenta que puede loguearse en un posnet. El objetivo y el acumulado son
    de referencia: no bloquean nada, solo se muestran en el backoffice para que el
    administrador sepa cuándo conviene cambiar de cuenta en el posnet."""

    nombre = models.CharField(
        verbose_name="Nombre",
        max_length=100,
        unique=True,
        help_text="Nombre identificatorio de la cuenta, ej: 'Juan Pérez - MercadoPago'",
    )
    objetivo_monto = models.PositiveIntegerField(
        verbose_name="Objetivo",
        default=0,
        help_text="Monto de referencia para esta cuenta. 0 = sin objetivo definido.",
    )
    acumulado_desde = models.DateTimeField(
        verbose_name="Acumula desde",
        default=timezone.now,
        help_text="Fecha desde la que se cuenta lo recibido contra el objetivo. "
        "El administrador la reinicia a mano cuando corresponde.",
    )
    is_active = models.BooleanField(verbose_name="Activa", default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Cuenta de posnet"
        verbose_name_plural = "Cuentas de posnet"

    def __str__(self):
        return self.nombre

    def get_acumulado_recibido(self):
        """Suma de lo RECIBIDO (Payment.amount) en pagos con tarjeta, QR o link de
        pago de esta cuenta, desde acumulado_desde. Un pago cancelado no cuenta: la
        plata no entró."""
        total_tarjeta = (
            Payment.objects.filter(
                detalle_tarjeta__cuenta=self,
                created_at__gte=self.acumulado_desde,
            )
            .exclude(estado=Payment.CANCELADO)
            .aggregate(total=models.Sum("amount"))["total"]
            or 0
        )
        total_posnet = (
            Payment.objects.filter(
                detalle_posnet__cuenta=self,
                created_at__gte=self.acumulado_desde,
            )
            .exclude(estado=Payment.CANCELADO)
            .aggregate(total=models.Sum("amount"))["total"]
            or 0
        )
        return total_tarjeta + total_posnet

    def get_porcentaje_objetivo(self):
        """None si no hay objetivo definido: no hay contra qué medir el acumulado."""
        if not self.objetivo_monto:
            return None
        return round(self.get_acumulado_recibido() * 100 / self.objetivo_monto)


class Posnet(models.Model):
    """Terminal física de cobro. `cuenta_actual` es la cuenta logeada hoy: el
    operador no la elige ni la ve, la carga el administrador desde el backoffice."""

    MERCADOPAGO = "MERCADOPAGO"
    PAYWAY = "PAYWAY"
    GETNET = "GETNET"
    OTRO = "OTRO"

    PROVEEDOR_CHOICES = [
        (MERCADOPAGO, "MercadoPago"),
        (PAYWAY, "Payway"),
        (GETNET, "Getnet"),
        (OTRO, "Otro"),
    ]

    nombre = models.CharField(
        verbose_name="Nombre",
        max_length=100,
        unique=True,
        help_text="Nombre identificatorio de la terminal, ej: 'Point Smart MercadoPago #1'",
    )
    proveedor = models.CharField(max_length=15, choices=PROVEEDOR_CHOICES, verbose_name="Proveedor")
    acepta_credito = models.BooleanField(
        verbose_name="Acepta crédito",
        default=False,
        help_text="Guía al operador al elegir el posnet. No bloquea la carga si no coincide.",
    )
    acepta_debito = models.BooleanField(
        verbose_name="Acepta débito",
        default=False,
        help_text="Guía al operador al elegir el posnet. No bloquea la carga si no coincide.",
    )
    acepta_qr = models.BooleanField(
        verbose_name="Acepta QR",
        default=False,
        help_text="Sin uso todavía. Queda listo para cuando se implemente el pago con QR.",
    )
    acepta_link_pago = models.BooleanField(
        verbose_name="Acepta link de pago",
        default=False,
        help_text="Sin uso todavía. Queda listo para cuando se implemente el link de pago.",
    )
    cuenta_actual = models.ForeignKey(
        CuentaPosnet,
        verbose_name="Cuenta logeada",
        on_delete=models.PROTECT,
        related_name="posnets",
        null=True,
        blank=True,
        help_text="Cuenta actualmente logeada en esta terminal.",
    )
    cuenta_actual_desde = models.DateTimeField(
        verbose_name="Logeada desde", null=True, blank=True, editable=False
    )
    is_active = models.BooleanField(verbose_name="Activo", default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Posnet"
        verbose_name_plural = "Posnets"

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        """Registra cuándo cambió la cuenta logeada, sin tocar la fecha en un save
        que no la cambia (ej: desactivar el posnet)."""
        if self.pk:
            cuenta_anterior_id = (
                Posnet.objects.filter(pk=self.pk).values_list("cuenta_actual_id", flat=True).first()
            )
        else:
            cuenta_anterior_id = None

        if cuenta_anterior_id != self.cuenta_actual_id:
            self.cuenta_actual_desde = timezone.now()

        super().save(*args, **kwargs)

    def get_monto_cobrado_cuenta_actual(self):
        """Cuánto se cobró en este posnet con la cuenta hoy logeada, desde que
        se logeó. Un pago cancelado no cuenta: la plata no entró."""
        if not self.cuenta_actual_id or not self.cuenta_actual_desde:
            return 0
        total = (
            self.pagos_tarjeta.filter(
                cuenta_id=self.cuenta_actual_id,
                payment__created_at__gte=self.cuenta_actual_desde,
            )
            .exclude(payment__estado=Payment.CANCELADO)
            .aggregate(total=models.Sum("monto_cobrado"))["total"]
        )
        return total or 0


class TarjetaPaymentDetail(models.Model):
    """Datos propios de un pago con tarjeta por posnet. Separado de Payment para no
    ensuciarlo con columnas que solo aplican a este método: mismo patrón sirve para
    QR y link de pago cuando lleguen."""

    CREDITO = "CREDITO"
    DEBITO = "DEBITO"

    TIPO_TARJETA_CHOICES = [
        (CREDITO, "Crédito"),
        (DEBITO, "Débito"),
    ]

    payment = models.OneToOneField(
        Payment,
        verbose_name="Pago",
        on_delete=models.PROTECT,
        related_name="detalle_tarjeta",
    )
    tipo_tarjeta = models.CharField(
        max_length=10, choices=TIPO_TARJETA_CHOICES, verbose_name="Tipo de tarjeta"
    )
    banco = models.ForeignKey(
        Banco,
        verbose_name="Banco emisor",
        on_delete=models.PROTECT,
        related_name="pagos_tarjeta",
    )
    ultimos_4_digitos = models.CharField(
        verbose_name="Últimos 4 dígitos",
        max_length=4,
        help_text="Los últimos 4 dígitos de la tarjeta del paciente",
    )
    posnet = models.ForeignKey(
        Posnet,
        verbose_name="Posnet",
        on_delete=models.PROTECT,
        related_name="pagos_tarjeta",
    )
    cuenta = models.ForeignKey(
        CuentaPosnet,
        verbose_name="Cuenta",
        on_delete=models.PROTECT,
        related_name="pagos_tarjeta",
        help_text="Cuenta que estaba logeada en el posnet al crear este pago. Es una foto "
        "del momento: no se actualiza sola si después cambia la cuenta del posnet.",
    )
    numero_operacion = models.CharField(
        verbose_name="Número de operación",
        max_length=50,
        help_text="Número de operación generado por el posnet",
    )
    monto_cobrado = models.PositiveIntegerField(
        verbose_name="Monto cobrado",
        help_text="Monto bruto, antes de la comisión del procesador. Informativo: los "
        "cálculos del sistema usan el monto recibido (el monto del pago).",
    )

    class Meta:
        verbose_name = "Detalle de pago con tarjeta"
        verbose_name_plural = "Detalles de pago con tarjeta"
        constraints = [
            models.UniqueConstraint(
                fields=["posnet", "numero_operacion"],
                name="unique_posnet_numero_operacion",
            )
        ]

    def __str__(self):
        return f"Tarjeta {self.get_tipo_tarjeta_display()} - {self.numero_operacion}"

    def clean(self):
        super().clean()
        errors = {}

        if self.tipo_tarjeta not in dict(self.TIPO_TARJETA_CHOICES):
            errors["tipo_tarjeta"] = "Tipo de tarjeta inválido."

        if not (self.numero_operacion or "").strip():
            errors["numero_operacion"] = "Número de operación es requerido."

        digitos = (self.ultimos_4_digitos or "").strip()
        if digitos and (not digitos.isdigit() or len(digitos) != 4):
            errors["ultimos_4_digitos"] = "Deben ser 4 dígitos numéricos."

        if self.posnet_id and not self.posnet.is_active:
            errors["posnet"] = "Este posnet está inactivo."

        if self.banco_id and not self.banco.is_active:
            errors["banco"] = "Este banco está inactivo."

        if self.payment_id and self.monto_cobrado and self.monto_cobrado < self.payment.amount:
            errors["monto_cobrado"] = "El monto cobrado no puede ser menor al monto recibido."

        if errors:
            raise ValidationError(errors)


class PosnetPaymentDetail(models.Model):
    """Datos propios de un pago con QR o link de pago por posnet. Un solo modelo
    para los dos métodos: tienen exactamente el mismo shape, se distinguen por
    payment.metodo (POSNET_QR o LINK_PAGO), no por un campo propio acá. Mismo
    patrón que TarjetaPaymentDetail, sin los campos propios de tarjeta."""

    payment = models.OneToOneField(
        Payment,
        verbose_name="Pago",
        on_delete=models.PROTECT,
        related_name="detalle_posnet",
    )
    posnet = models.ForeignKey(
        Posnet,
        verbose_name="Posnet",
        on_delete=models.PROTECT,
        related_name="pagos_posnet",
    )
    cuenta = models.ForeignKey(
        CuentaPosnet,
        verbose_name="Cuenta",
        on_delete=models.PROTECT,
        related_name="pagos_posnet",
        help_text="Cuenta que estaba logeada en el posnet al crear este pago. Es una foto "
        "del momento: no se actualiza sola si después cambia la cuenta del posnet.",
    )
    numero_operacion = models.CharField(
        verbose_name="Número de operación",
        max_length=50,
        help_text="Número de operación generado por el posnet",
    )
    monto_cobrado = models.PositiveIntegerField(
        verbose_name="Monto cobrado",
        help_text="Monto bruto, antes de la comisión del procesador. Informativo: los "
        "cálculos del sistema usan el monto recibido (el monto del pago).",
    )

    class Meta:
        verbose_name = "Detalle de pago con QR o link de pago"
        verbose_name_plural = "Detalles de pago con QR o link de pago"
        constraints = [
            models.UniqueConstraint(
                fields=["posnet", "numero_operacion"],
                name="unique_posnet_numero_operacion_posnet_detail",
            )
        ]

    def __str__(self):
        return f"{self.payment.get_metodo_display()} - {self.numero_operacion}"

    def clean(self):
        super().clean()
        errors = {}

        if not (self.numero_operacion or "").strip():
            errors["numero_operacion"] = "Número de operación es requerido."

        if self.posnet_id and not self.posnet.is_active:
            errors["posnet"] = "Este posnet está inactivo."

        if self.payment_id and self.monto_cobrado and self.monto_cobrado < self.payment.amount:
            errors["monto_cobrado"] = "El monto cobrado no puede ser menor al monto recibido."

        if errors:
            raise ValidationError(errors)


class ProfessionalPaymentStats(User):
    """Proxy model to show payment statistics for professionals.

    Todas las cifras de acá son plata cobrada: cuentan solo pagos COMPLETADO. Un
    pendiente todavía no entró y un cancelado nunca va a entrar."""

    class Meta:
        proxy = True
        verbose_name = "Estadísticas por odontólogo"
        verbose_name_plural = "Estadísticas por odontólogo"

    def _completed_payments(self):
        return self.professional_payments.filter(estado=Payment.COMPLETADO)

    def get_total_payments_amount(self):
        """Get total amount of all payments for this professional"""
        return self._completed_payments().aggregate(total=models.Sum("amount"))["total"] or 0

    def get_total_payments_count(self):
        """Get total count of payments for this professional"""
        return self._completed_payments().count()

    def get_current_month_payments_amount(self, year=None, month=None):
        """Get total amount of payments for a specific month (defaults to current month)"""
        now = get_now_arg()
        year = year or now.year
        month = month or now.month
        return (
            self._completed_payments()
            .filter(created_at__year=year, created_at__month=month)
            .aggregate(total=models.Sum("amount"))["total"]
            or 0
        )

    def get_current_month_payments_count(self, year=None, month=None):
        """Get count of payments for a specific month (defaults to current month)"""
        now = get_now_arg()
        year = year or now.year
        month = month or now.month
        return (
            self._completed_payments()
            .filter(created_at__year=year, created_at__month=month)
            .count()
        )


class RecipientPaymentStats(PaymentRecipient):
    """Proxy model to show payment statistics for recipients with date hierarchy.

    Todas las cifras de acá son plata recibida: cuentan solo pagos COMPLETADO. Para
    saber cuánto cupo queda, que sí incluye pendientes, están get_remaining_amount
    y get_remaining_weekly_amount en PaymentRecipient."""

    class Meta:
        proxy = True
        verbose_name = "Estadísticas por destinatario"
        verbose_name_plural = "Estadísticas por destinatario"

    def _completed_payments(self):
        return self.payments.filter(estado=Payment.COMPLETADO)

    def get_total_payments_amount(self):
        """Get total amount of all payments received by this recipient"""
        return self._completed_payments().aggregate(total=models.Sum("amount"))["total"] or 0

    def get_total_payments_count(self):
        """Get total count of payments received by this recipient"""
        return self._completed_payments().count()

    def get_period_payments_amount(self, year=None, month=None, day=None):
        """Get total amount of payments for a specific period (year/month/day)"""
        queryset = self._completed_payments()

        if year:
            queryset = queryset.filter(created_at__year=year)
        if month:
            queryset = queryset.filter(created_at__month=month)
        if day:
            queryset = queryset.filter(created_at__day=day)

        return queryset.aggregate(total=models.Sum("amount"))["total"] or 0

    def get_period_payments_count(self, year=None, month=None, day=None):
        """Get count of payments for a specific period (year/month/day)"""
        queryset = self._completed_payments()

        if year:
            queryset = queryset.filter(created_at__year=year)
        if month:
            queryset = queryset.filter(created_at__month=month)
        if day:
            queryset = queryset.filter(created_at__day=day)

        return queryset.count()


class PosnetAccountPaymentStats(CuentaPosnet):
    """Proxy model para ver cuánto generó una cuenta de posnet en un período,
    con date hierarchy. Mismo patrón que RecipientPaymentStats.

    Cuenta plata RECIBIDA (Payment.amount), solo pagos COMPLETADO, igual que el
    resto de las estadísticas del sistema."""

    class Meta:
        proxy = True
        verbose_name = "Estadísticas por cuenta de posnet"
        verbose_name_plural = "Estadísticas por cuenta de posnet"

    def _completed_payments(self):
        return Payment.objects.filter(detalle_tarjeta__cuenta=self, estado=Payment.COMPLETADO)

    def get_total_payments_amount(self):
        return self._completed_payments().aggregate(total=models.Sum("amount"))["total"] or 0

    def get_total_payments_count(self):
        return self._completed_payments().count()

    def get_period_payments_amount(self, year=None, month=None, day=None):
        queryset = self._completed_payments()

        if year:
            queryset = queryset.filter(created_at__year=year)
        if month:
            queryset = queryset.filter(created_at__month=month)
        if day:
            queryset = queryset.filter(created_at__day=day)

        return queryset.aggregate(total=models.Sum("amount"))["total"] or 0

    def get_period_payments_count(self, year=None, month=None, day=None):
        queryset = self._completed_payments()

        if year:
            queryset = queryset.filter(created_at__year=year)
        if month:
            queryset = queryset.filter(created_at__month=month)
        if day:
            queryset = queryset.filter(created_at__day=day)

        return queryset.count()


class DepositRecipient(PaymentRecipient):
    """Proxy model for deposit-only recipient management"""

    class Meta:
        proxy = True
        verbose_name = "Destinatario de señas"
        verbose_name_plural = "Destinatarios de señas"


class AutomationRule(models.Model):
    class RuleType(models.TextChoices):
        WEEKDAY = "WEEKDAY", _("Día de la semana")
        MONTHDAY = "MONTHDAY", _("Día del mes")

    class ActionType(models.TextChoices):
        SET_PRIORITY = "SET_PRIORITY", _("Establecer prioridad")
        SET_ACTIVE_STATUS = "SET_ACTIVE_STATUS", _("Establecer estado activo")

    class PriorityPosition(models.TextChoices):
        FIRST = "FIRST", _("Primero")
        LAST = "LAST", _("Último")
        MIDDLE = "MIDDLE", _("Medio")
        SPECIFIC = "SPECIFIC", _("Específico")

    class Weekday(models.IntegerChoices):
        MONDAY = 0, _("Lunes")
        TUESDAY = 1, _("Martes")
        WEDNESDAY = 2, _("Miércoles")
        THURSDAY = 3, _("Jueves")
        FRIDAY = 4, _("Viernes")
        SATURDAY = 5, _("Sábado")
        SUNDAY = 6, _("Domingo")

    name = models.CharField(max_length=200)
    payment_recipient = models.ForeignKey(
        PaymentRecipient, on_delete=models.CASCADE, related_name="automation_rules"
    )
    is_active = models.BooleanField(default=True)
    rule_type = models.CharField(max_length=20, choices=RuleType.choices)
    trigger_weekday = models.PositiveIntegerField(
        null=True, blank=True, choices=Weekday.choices, help_text="Día de la semana"
    )
    trigger_monthday = models.PositiveIntegerField(null=True, blank=True, help_text="1..28")
    trigger_time = models.TimeField(default=timezone.datetime.strptime("00:00", "%H:%M").time())
    action_type = models.CharField(max_length=30, choices=ActionType.choices)
    priority_value = models.PositiveIntegerField(null=True, blank=True)
    priority_position = models.CharField(
        max_length=20, null=True, blank=True, choices=PriorityPosition.choices
    )
    active_value = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_executed = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "rule_type"]),
            models.Index(fields=["payment_recipient"]),
        ]
        verbose_name = "Regla de automatización"
        verbose_name_plural = "Reglas de automatización"

    def __str__(self):
        return f"{self.name}"

    def clean(self):
        super().clean()

        errors = {}
        if self.rule_type == AutomationRule.RuleType.WEEKDAY:
            if self.trigger_weekday is None or self.trigger_weekday < 0 or self.trigger_weekday > 6:
                errors["trigger_weekday"] = "Debe especificar un día de la semana entre 0 y 6."
        if self.rule_type == AutomationRule.RuleType.MONTHDAY:
            if (
                self.trigger_monthday is None
                or self.trigger_monthday < 1
                or self.trigger_monthday > 31
            ):
                errors["trigger_monthday"] = "Debe especificar un día del mes entre 1 y 31."

        if self.action_type == AutomationRule.ActionType.SET_PRIORITY:
            if not self.priority_position:
                errors["priority_position"] = "Debe especificar la posición de prioridad."
            if (
                self.priority_position == AutomationRule.PriorityPosition.SPECIFIC
                and self.priority_value in (None, 0)
            ):
                errors["priority_value"] = (
                    'Debe especificar un valor de prioridad válido para "SPECIFIC".'
                )

        if self.action_type == AutomationRule.ActionType.SET_ACTIVE_STATUS:
            if self.active_value is None:
                errors["active_value"] = "Debe especificar el estado activo."

        # prevent conflicting rules same recipient/time
        if self.pk is None and self.is_active and self.payment_recipient_id:
            qs = AutomationRule.objects.filter(
                is_active=True,
                payment_recipient=self.payment_recipient,
                rule_type=self.rule_type,
                trigger_time=self.trigger_time,
            )
            if self.rule_type == AutomationRule.RuleType.WEEKDAY:
                qs = qs.filter(trigger_weekday=self.trigger_weekday)
            else:
                qs = qs.filter(trigger_monthday=self.trigger_monthday)
            if qs.exists():
                errors["name"] = "Ya existe una regla activa para el mismo destinatario y horario."

        if errors:
            raise ValidationError(errors)


class RuleExecutionLog(models.Model):
    rule = models.ForeignKey(
        AutomationRule, on_delete=models.CASCADE, related_name="execution_logs"
    )
    payment_recipient = models.ForeignKey(
        PaymentRecipient, on_delete=models.CASCADE, related_name="rule_execution_logs"
    )
    executed_at = models.DateTimeField(auto_now_add=True)
    action_taken = models.CharField(max_length=200)
    old_value = models.CharField(max_length=100, null=True)
    new_value = models.CharField(max_length=100, null=True)
    success = models.BooleanField(default=True)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["executed_at"]),
            models.Index(fields=["success"]),
        ]
        # Hide in django admin
        verbose_name = "Ejecución automatica"
        verbose_name_plural = "Historial de ejecuciones"

    def __str__(self):
        return f"{self.rule.name} @ {self.executed_at:%Y-%m-%d %H:%M}"
