from datetime import timedelta

from django.contrib import admin
from django.utils.formats import date_format
from django.utils import timezone
from django.utils.text import slugify
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponse, JsonResponse
from django.urls import path, reverse
import csv
import types
import zipfile
from io import BytesIO
import os
from adminsortable2.admin import SortableAdminMixin
from .models import (
    User,
    PaymentRecipient,
    Payment,
    ProfessionalPaymentStats,
    RecipientPaymentStats,
    AutomationRule,
    RuleExecutionLog,
    DepositRecipient,
    Banco,
    CuentaPosnet,
    Posnet,
    PosnetAccountPaymentStats,
    PosnetPaymentDetail,
    TarjetaPaymentDetail,
)
from .tasks import execute_single_rule
from .utils.utils import format_money_thousand_separator


OPERATOR_PAYMENT_HISTORY_DAYS = 15


def _operator_payment_history_cutoff():
    return timezone.now() - timedelta(days=OPERATOR_PAYMENT_HISTORY_DAYS)


def _filter_queryset_for_operator_payments(qs, user):
    if user.has_perm("payment_instructions.view_all_payment_history_in_admin"):
        return qs  # bypass total: todos los pagos/señas históricos
    if user.has_perm("payment_instructions.view_all_deposit_payments_in_admin"):
        qs = qs.filter(Q(operator_user=user) | Q(payment_type=Payment.DEPOSIT))
    else:
        qs = qs.filter(operator_user=user)
    return qs.filter(created_at__gte=_operator_payment_history_cutoff())


class ProfessionalFullNameFilter(admin.SimpleListFilter):
    title = "Profesional"
    parameter_name = "professional"

    def lookups(self, request, model_admin):
        professionals = User.objects.filter(role=User.PROFESSIONAL, is_active=True).order_by(
            "first_name", "last_name"
        )
        return [
            (
                professional.id,
                f"{professional.first_name} {professional.last_name}".strip()
                or professional.username,
            )
            for professional in professionals
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(professional_id=self.value())
        return queryset


class OperatorFullNameFilter(admin.SimpleListFilter):
    title = "Creador"
    parameter_name = "operator_user"

    def lookups(self, request, model_admin):
        operators = User.objects.filter(role=User.OPERATOR, is_active=True).order_by(
            "first_name", "last_name"
        )
        admin = User.objects.filter(role=User.ADMINISTRATOR, is_active=True).order_by(
            "first_name", "last_name"
        )
        operators = operators | admin
        return [
            (
                operator.id,
                f"{operator.first_name} {operator.last_name}".strip() or operator.username,
            )
            for operator in operators
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(operator_user_id=self.value())
        return queryset


class ReviewedListFilter(admin.SimpleListFilter):
    title = "Revisado"
    parameter_name = "reviewed"

    def lookups(self, request, model_admin):
        return (
            ("1", "Revisados"),
            ("0", "No revisados"),
        )

    def queryset(self, request, queryset):
        v = self.value()
        if v == "1":
            return queryset.filter(reviewed=True)
        if v == "0":
            return queryset.filter(reviewed=False)
        return queryset


class ActiveRecipientFilter(admin.SimpleListFilter):
    """Destinatarios: sin parametro en la URL, el listado muestra solo los activos."""

    title = "Estado"
    parameter_name = "estado"

    def lookups(self, request, model_admin):
        return (
            ("inactivos", "Inactivos"),
            ("todos", "Todos"),
        )

    def queryset(self, request, queryset):
        v = self.value()
        if v == "todos":
            return queryset
        if v == "inactivos":
            return queryset.filter(is_active=False)
        return queryset.filter(is_active=True)

    def choices(self, changelist):
        # La opcion vacia de Django dice "Todos". Aca el vacio filtra por activos.
        opciones = list(super().choices(changelist))
        opciones[0]["display"] = "Activos"
        return opciones


class ActiveRecipientRelatedFilter(admin.RelatedFieldListFilter):
    """Filtro lateral de destinatario: lista solo los activos, para no llenarlo de bajas."""

    def field_choices(self, field, request, model_admin):
        return field.get_choices(
            include_blank=False,
            limit_choices_to={"is_active": True},
        )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "role", "is_staff", "is_active", "date_joined")
    list_filter = ("role", "is_staff", "is_active", "date_joined")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)

    fieldsets = BaseUserAdmin.fieldsets + (("Información del rol", {"fields": ("role",)}),)

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "password1",
                    "password2",
                ),
            },
        ),
        (
            "Información personal",
            {
                "fields": (
                    "first_name",
                    "last_name",
                )
            },
        ),
        (
            "Información del rol",
            {
                "fields": (
                    "role",
                    "is_staff",
                )
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Administrators can see all users
        if hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR:
            return qs
        # Operators and professionals can only see themselves
        return qs.filter(pk=request.user.pk)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        # Professionals should not see User admin in menu
        return hasattr(request.user, "role") and request.user.role != User.PROFESSIONAL


@admin.register(PaymentRecipient)
class PaymentRecipientAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = (
        "priority_order",
        "name",
        "max_amount_display",
        "current_month_received",
        "current_month_remaining",
        "alias",
        "is_active",
    )
    list_filter = (
        ActiveRecipientFilter,
        "created_at",
    )
    search_fields = ("name", "alias", "cbu")
    ordering = ("priority_order", "name")
    readonly_fields = (
        "created_at",
        "updated_at",
        "current_week_received",
        "current_week_remaining",
        "current_month_received",
        "remaining_amount",
        "received_per_week_of_month",
        "rules_count",
        "last_rule_execution",
    )

    fieldsets = (
        ("Información básica", {"fields": ("name", "alias", "cbu", "recipient_type", "notes")}),
        (
            "Configuración de pago",
            {
                "fields": (
                    "max_amount",
                    "weekly_cap_amount",
                    "min_threshold",
                    "priority_order",
                    "is_active",
                ),
                "description": 'Nota: Los destinatarios "Solo para Señas" no tienen límites mensuales ni semanales. Solo puede existir un destinatario activo de este tipo.',
            },
        ),
        (
            "Estado actual",
            {
                "fields": (
                    "current_week_received",
                    "current_week_remaining",
                    "current_month_received",
                    "remaining_amount",
                ),
            },
        ),
        ("Reglas de automatización", {"fields": ("rules_count", "last_rule_execution")}),
    )

    def get_queryset(self, request):
        """Exclude DEPOSIT_ONLY recipients from regular list"""
        qs = super().get_queryset(request)
        # Professionals should not see PaymentRecipient admin at all
        if hasattr(request.user, "role") and request.user.role == User.PROFESSIONAL:
            return qs.none()
        # Exclude deposit-only recipients from regular list
        return qs.filter(recipient_type=PaymentRecipient.REGULAR)

    def get_form(self, request, obj=None, **kwargs):
        form = super(PaymentRecipientAdmin, self).get_form(request, obj, **kwargs)
        form.base_fields["max_amount"].widget.attrs["style"] = "width: 20em;"
        if "weekly_cap_amount" in form.base_fields:
            form.base_fields["weekly_cap_amount"].widget.attrs["style"] = "width: 20em;"
        form.base_fields["min_threshold"].widget.attrs["style"] = "width: 20em;"
        form.base_fields["priority_order"].widget.attrs["style"] = "width: 4em;"

        # Set default priority_order to the next available value for new objects
        if not obj and "priority_order" in form.base_fields:
            max_priority = PaymentRecipient.objects.aggregate(
                max_priority=models.Max("priority_order")
            )["max_priority"]
            next_priority = (max_priority or 0) + 1
            form.base_fields["priority_order"].initial = next_priority

        if "notes" in form.base_fields:
            form.base_fields["notes"].widget.attrs["style"] = "width: 40em;"
            form.base_fields["notes"].widget.attrs["rows"] = 1

        return form

    actions = ["activate_recipients", "deactivate_recipients"]

    def max_amount_display(self, obj):
        return f"${format_money_thousand_separator(obj.max_amount)}"

    max_amount_display.short_description = "Monto máximo"

    def weekly_cap_display(self, obj):
        cap = obj.weekly_cap_amount or 0
        if cap <= 0:
            return "Sin límite"
        return f"${format_money_thousand_separator(cap)}"

    weekly_cap_display.short_description = "Tope semanal"

    def current_week_received(self, obj):
        amount = obj.get_current_week_received()
        return f"${format_money_thousand_separator(amount)}"

    current_week_received.short_description = "Cupo consumido esta semana"

    def current_week_remaining(self, obj):
        week_remaining = obj.get_remaining_weekly_amount()
        month_remaining = obj.get_remaining_amount()
        if week_remaining is None:
            return "Sin límite"
        # Use the lower of the two values if month remaining is less than week remaining
        display_remaining = min(week_remaining, month_remaining)
        return f"${format_money_thousand_separator(display_remaining)}"

    current_week_remaining.short_description = "Restante esta semana"

    def received_per_week_of_month(self, obj):
        # show a list of the amount received per week of the month
        amount = obj.get_received_per_week_of_month()
        return "\n".join(
            [
                f"Semana {week + 1}: ${format_money_thousand_separator(amount)}"
                for week, amount in enumerate(amount)
            ]
        )

    received_per_week_of_month.short_description = "Cupo consumido por semana de este mes"

    def current_month_received(self, obj):
        amount = obj.get_current_month_received()
        return f"${format_money_thousand_separator(amount)}"

    current_month_received.short_description = "Cupo consumido este mes"

    def current_month_remaining(self, obj):
        amount = obj.get_remaining_amount()
        return f"${format_money_thousand_separator(amount)}"

    current_month_remaining.short_description = "Saldo restante este mes"

    def remaining_amount(self, obj):
        remaining = obj.get_remaining_amount()
        return f"${format_money_thousand_separator(remaining)}"

    remaining_amount.short_description = "Restante este mes"

    def rules_count(self, obj):
        return obj.automation_rules.filter(is_active=True).count()

    rules_count.short_description = "Reglas activas"

    def last_rule_execution(self, obj):
        log = obj.rule_execution_logs.order_by("-executed_at").first()
        if not log:
            return "—"
        return date_format(log.executed_at, "DATETIME_FORMAT")

    last_rule_execution.short_description = "Última ejecución"

    def activate_recipients(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Activated {updated} recipients.")

    activate_recipients.short_description = "Activar seleccionados"

    def deactivate_recipients(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Deactivated {updated} recipients.")

    deactivate_recipients.short_description = "Desactivar seleccionados"

    def save_model(self, request, obj, form, change):
        """Override save to validate deposit recipient uniqueness"""
        try:
            obj.clean()
        except ValidationError as e:
            from django.contrib import messages

            for field, errors in e.message_dict.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            return
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        # Only administrators can manage payment recipients
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        # Only administrators can manage payment recipients
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        # Only administrators can manage payment recipients
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        # Allow administrators and operators, but not professionals
        return hasattr(request.user, "role") and request.user.role in [
            User.ADMINISTRATOR,
            User.OPERATOR,
        ]


@admin.register(DepositRecipient)
class DepositRecipientAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "alias",
        "cbu",
        "is_active",
        "current_month_received_deposits",
        "created_at",
    )
    search_fields = ("name", "alias", "cbu")
    readonly_fields = (
        "created_at",
        "updated_at",
        "current_month_received_deposits",
        "total_deposits_count",
    )
    exclude = ("max_amount", "weekly_cap_amount", "min_threshold", "priority_order")

    fieldsets = (
        (
            "Información básica",
            {
                "fields": ("name", "alias", "cbu", "recipient_type", "notes", "is_active"),
                "description": 'Nota: Cambiar el tipo a "Destinatario Regular" moverá este destinatario a la lista normal de destinatarios. NO es recomendable.',
            },
        ),
        (
            "Estadísticas de señas",
            {
                "fields": (
                    "current_month_received_deposits",
                    "total_deposits_count",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        """Only show DEPOSIT_ONLY recipient"""
        qs = super().get_queryset(request)
        # Professionals should not see this admin
        if hasattr(request.user, "role") and request.user.role == User.PROFESSIONAL:
            return qs.none()
        # Only show deposit-only recipients
        return qs.filter(recipient_type=PaymentRecipient.DEPOSIT_ONLY)

    def current_month_received_deposits(self, obj):
        """Get total amount of deposits received this month"""
        from .models import Payment, get_now_arg
        from zoneinfo import ZoneInfo

        now_arg = get_now_arg()
        start_of_month_arg = now_arg.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_of_month_utc = start_of_month_arg.astimezone(ZoneInfo("UTC"))

        total = (
            obj.payments.filter(
                created_at__gte=start_of_month_utc, payment_type=Payment.DEPOSIT
            ).aggregate(total=models.Sum("amount"))["total"]
            or 0
        )

        return f"${format_money_thousand_separator(total)}"

    current_month_received_deposits.short_description = "Señas recibidas este mes"

    def total_deposits_count(self, obj):
        """Get total count of deposits"""
        from .models import Payment

        return obj.payments.filter(payment_type=Payment.DEPOSIT).count()

    total_deposits_count.short_description = "Total de señas (histórico)"

    def has_add_permission(self, request):
        """Only allow adding if no deposit recipient exists"""
        if request.user.is_superuser:
            # Check if a deposit recipient already exists
            if PaymentRecipient.objects.filter(
                recipient_type=PaymentRecipient.DEPOSIT_ONLY
            ).exists():
                return False
            return True
        if hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR:
            # Check if a deposit recipient already exists
            if PaymentRecipient.objects.filter(
                recipient_type=PaymentRecipient.DEPOSIT_ONLY
            ).exists():
                return False
            return True
        return False

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        # Allow administrators and operators, but not professionals
        return hasattr(request.user, "role") and request.user.role in [
            User.ADMINISTRATOR,
            User.OPERATOR,
        ]

    def save_model(self, request, obj, form, change):
        """Validate before saving"""
        # If creating new, force to DEPOSIT_ONLY and set default values
        if not change:
            obj.recipient_type = PaymentRecipient.DEPOSIT_ONLY
            obj.max_amount = 1
            obj.weekly_cap_amount = 0
            obj.min_threshold = 0
            obj.priority_order = 99999  # Low priority since it's not used in auto-assignment

        try:
            obj.clean()
        except ValidationError as e:
            from django.contrib import messages

            for field, errors in e.message_dict.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            return
        super().save_model(request, obj, form, change)


class EstadoListFilter(admin.SimpleListFilter):
    """Filtro de estado que arranca en Completado en vez de en Todos.

    El listado de pagos se usa para auditar plata que entró. Los pendientes son
    trabajo en curso y los cancelados son ruido para esa pregunta, así que el default
    los deja afuera; se ven eligiendo el estado, o "Todos"."""

    title = "Estado"
    parameter_name = "estado"

    def lookups(self, request, model_admin):
        return Payment.ESTADO_CHOICES

    def queryset(self, request, queryset):
        valor = self.value()
        if valor is None:
            return queryset.filter(estado=Payment.COMPLETADO)
        if valor == "todos":
            return queryset
        return queryset.filter(estado=valor)

    def choices(self, changelist):
        # Sin esto, Django marcaría "Todos" como opción activa cuando no hay parámetro
        # en la URL, y estaría mintiendo: lo que se ve son solo los completados.
        yield {
            "selected": self.value() == "todos",
            "query_string": changelist.get_query_string({self.parameter_name: "todos"}),
            "display": "Todos",
        }
        for valor, etiqueta in self.lookup_choices:
            yield {
                "selected": self.value() == valor
                or (self.value() is None and valor == Payment.COMPLETADO),
                "query_string": changelist.get_query_string({self.parameter_name: valor}),
                "display": etiqueta,
            }


class PaymentTypeFilter(admin.SimpleListFilter):
    title = "Tipo de pago"
    parameter_name = "payment_type"

    def lookups(self, request, model_admin):
        return Payment.PAYMENT_TYPE_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(payment_type=self.value())
        return queryset


class TarjetaPaymentDetailInline(admin.StackedInline):
    model = TarjetaPaymentDetail
    extra = 0
    can_delete = False
    max_num = 1


class PosnetPaymentDetailInline(admin.StackedInline):
    model = PosnetPaymentDetail
    extra = 0
    can_delete = False
    max_num = 1


class _RequiredOnAddInlineFormSet(BaseInlineFormSet):
    """Para el alta de un pago: el padre (Payment) todavía no existe en la base
    cuando se arma este formset, así que no hay forma de saber de antemano si el
    método elegido va a necesitar este detalle. Se muestran los dos inlines
    (tarjeta y posnet), sin ninguna fila por defecto — el administrador agrega
    una con el "Agregar otro" nativo del admin si el método que eligió la
    necesita. Acá se exige esa fila solo si el método enviado es el que le
    corresponde a este inline.

    Django no marca como inválido un formset sin filas por defecto — sin este
    clean(), un administrador distraído deja el detalle sin cargar y el pago se
    guarda igual, sin tarjeta/posnet, exactamente el agujero que este inline
    vino a tapar."""

    required_metodos = ()

    def clean(self):
        super().clean()
        if self.instance.pk or self.instance.metodo not in self.required_metodos:
            return
        tiene_fila = any(
            form.cleaned_data and not form.cleaned_data.get("DELETE", False) for form in self.forms
        )
        if not tiene_fila:
            raise ValidationError(
                f"Un pago con método {self.instance.get_metodo_display()} necesita "
                'su detalle. Hacé clic en "Agregar otro" para cargarlo.'
            )


class _TarjetaRequiredOnAddFormSet(_RequiredOnAddInlineFormSet):
    required_metodos = (Payment.POSNET_TARJETA,)


class _PosnetRequiredOnAddFormSet(_RequiredOnAddInlineFormSet):
    required_metodos = (Payment.POSNET_QR, Payment.LINK_PAGO)


class TarjetaPaymentDetailInlineOnAdd(TarjetaPaymentDetailInline):
    """Variante del inline de tarjeta para el alta: no se ve ninguna fila por
    defecto (como en la edición), pero está disponible para completar en el
    mismo formulario con el "Agregar otro" nativo del admin, en vez de tener
    que crear el pago primero y recién después editarlo para cargar el
    detalle."""

    formset = _TarjetaRequiredOnAddFormSet


class PosnetPaymentDetailInlineOnAdd(PosnetPaymentDetailInline):
    """Igual que TarjetaPaymentDetailInlineOnAdd, para QR y link de pago."""

    formset = _PosnetRequiredOnAddFormSet


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_filter = (
        ReviewedListFilter,
        EstadoListFilter,
        "metodo",
        "payment_type",
        ("payment_recipient", ActiveRecipientRelatedFilter),
        OperatorFullNameFilter,
        ProfessionalFullNameFilter,
    )
    search_fields = (
        "amount",
        "patient_name",
        "payment_recipient__name",
        "payment_recipient__alias",
        "operator_user__username",
        "professional__username",
        "professional__first_name",
        "professional__last_name",
        "observaciones",
    )
    ordering = ("-created_at",)
    readonly_fields = ("preview_proof", "cancelado_at", "cancelado_por")
    date_hierarchy = "created_at"
    fieldsets = (
        (
            "Información de pago",
            {
                "fields": (
                    "payment_type",
                    "metodo",
                    "estado",
                    "amount",
                    "patient_name",
                    "payment_recipient",
                    "professional",
                    "operator_user",
                    "created_at",
                ),
                "description": 'Nota: Los pagos tipo "Seña" solo pueden asignarse a destinatarios de tipo "Solo para Señas". Los pagos "Regular" solo pueden asignarse a destinatarios regulares.',
            },
        ),
        ("Documentación", {"fields": ("proof_of_payment_file", "observaciones", "preview_proof")}),
        (
            "Revisado",
            {
                "fields": ("reviewed",),
                "description": (
                    "Marque la casilla cuando haya revisado este pago en el administrador. "
                    "Así se distingue lo ya visto y puede filtrar el listado entre pagos revisados y pendientes."
                ),
            },
        ),
        (
            "Cancelación",
            {
                # cancelacion-fieldset: la usa payment_cancelacion_toggle.js para
                # mostrar/ocultar esta sección según el Estado elegido. No tiene
                # sentido verla en un pago que no está cancelado — pero tiene que
                # seguir existiendo en el DOM: es la única forma de cancelar a mano
                # un pago ya cobrado (ver cancelar_pagos, más abajo).
                "classes": ("cancelacion-fieldset",),
                "fields": (
                    "motivo_cancelacion",
                    "observacion_cancelacion",
                    "cancelado_at",
                    "cancelado_por",
                ),
                "description": (
                    "Solo aplica a pagos cancelados. Un pago cancelado libera el cupo que "
                    "ocupaba, pero no se borra: queda el rastro de quién canceló y por qué."
                ),
            },
        ),
    )

    class Media:
        js = ("admin/payment_instructions/js/payment_cancelacion_toggle.js",)

    def get_list_display(self, request):
        """
        Dynamically return list_display based on the logged-in user's role.
        """
        user = request.user

        # If superuser
        if user.is_superuser:
            return [
                "id",
                "amount_display",
                "metodo",
                "patient_name_display",
                "professional_display",
                "payment_recipient_display",
                "operator_user_display",
                "has_proof",
                "created_at_display",
                "reviewed_display",
            ]

        # If user is a professional
        if user.is_professional():
            return [
                "id",
                "patient_name_display",
                "payment_recipient_display",
                "operator_user_display",
                "has_proof",
                "created_at_display",
                "reviewed_display",
            ]

        # Default for others
        return [
            "id",
            "amount_display",
            "metodo",
            "patient_name_display",
            "payment_recipient_display",
            "operator_user_display",
            "has_proof",
            "created_at_display",
            "reviewed_display",
        ]

    def _can_edit_payment_fully(self, request):
        if request.user.is_superuser:
            return True
        return getattr(request.user, "role", None) == User.ADMINISTRATOR

    def _can_mark_reviewed(self, request):
        return request.user.has_perm("payment_instructions.mark_payment_reviewed")

    def _user_may_only_mark_reviewed(self, request):
        return self._can_mark_reviewed(request) and not self._can_edit_payment_fully(request)

    def _can_toggle_reviewed(self, request):
        return self._can_edit_payment_fully(request) or self._can_mark_reviewed(request)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "toggle-reviewed/",
                self.admin_site.admin_view(self.toggle_reviewed_view),
                name="payment_instructions_payment_toggle_reviewed",
            ),
        ]
        return custom_urls + urls

    def toggle_reviewed_view(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "Método no permitido"}, status=405)
        if not self._can_toggle_reviewed(request):
            return JsonResponse({"error": "Sin permiso"}, status=403)

        payment_id = request.POST.get("payment_id")
        if not payment_id:
            return JsonResponse({"error": "ID de pago requerido"}, status=400)

        reviewed = request.POST.get("reviewed") in ("true", "1", "on")

        try:
            payment = self.get_queryset(request).get(pk=payment_id)
        except Payment.DoesNotExist:
            return JsonResponse({"error": "Pago no encontrado"}, status=404)

        payment.reviewed = reviewed
        payment.save(update_fields=["reviewed"])
        return JsonResponse({"ok": True, "reviewed": payment.reviewed})

    @staticmethod
    def _user_display_name(user):
        if not user:
            return ""
        return f"{user.first_name} {user.last_name}".strip() or user.username

    def preview_proof(self, obj):
        """Show file preview inside admin form."""
        if not obj or not obj.proof_of_payment_file:
            return "Sin archivo"

        url = obj.proof_of_payment_file.url

        # For images → show thumbnail
        if obj.proof_of_payment_file.name.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
            return format_html('<img src="{}" style="max-height:200px; border-radius:8px;" />', url)

        # For PDFs → simple link or icon (basic version)
        return format_html('<a href="{}" target="_blank">📄 Ver PDF</a>', url)

    preview_proof.short_description = "Vista previa"

    @admin.display(description="Monto", ordering="amount")
    def amount_display(self, obj):
        return f"${format_money_thousand_separator(obj.amount)}"

    @admin.display(description="Paciente", ordering="patient_name")
    def patient_name_display(self, obj):
        return obj.patient_name

    @admin.display(description="Profesional", ordering="professional")
    def professional_display(self, obj):
        return self._user_display_name(obj.professional)

    @admin.display(description="Destinatario", ordering="payment_recipient")
    def payment_recipient_display(self, obj):
        return str(obj.payment_recipient) if obj.payment_recipient_id else "—"

    @admin.display(description="Operador", ordering="operator_user")
    def operator_user_display(self, obj):
        return self._user_display_name(obj.operator_user)

    @admin.display(description="Creado", ordering="created_at")
    def created_at_display(self, obj):
        if not obj.created_at:
            return ""
        return date_format(timezone.localtime(obj.created_at), "SHORT_DATETIME_FORMAT")

    @admin.display(description="Revisado", ordering="reviewed")
    def reviewed_display(self, obj):
        request = getattr(self, "_changelist_request", None)
        if request and self._can_toggle_reviewed(request):
            if obj.reviewed:
                return format_html(
                    '<input type="checkbox" class="payment-reviewed-toggle" '
                    'data-payment-id="{}" checked aria-label="Revisado">',
                    obj.pk,
                )
            return format_html(
                '<input type="checkbox" class="payment-reviewed-toggle" '
                'data-payment-id="{}" aria-label="Revisado">',
                obj.pk,
            )
        if obj.reviewed:
            return format_html(
                '<span data-payment-reviewed="1" aria-hidden="true">✓</span>',
            )
        return format_html('<span aria-hidden="true">—</span>')

    def has_proof(self, obj):
        if obj.proof_of_payment_file:
            return format_html(
                '<a href="{}" target="_blank">Abrir archivo</a>',
                obj.proof_of_payment_file.url,
            )
        return "Sin archivo"

    has_proof.short_description = "Comprobante"

    def save_model(self, request, obj, form, change):
        if change and self._user_may_only_mark_reviewed(request):
            payment = self.model.objects.get(pk=obj.pk)
            payment.reviewed = form.cleaned_data.get("reviewed", False)
            payment.save(update_fields=["reviewed"])
            return

        if not change:  # Creating new payment
            obj.operator_user = request.user

        # Alta nueva: get_inline_instances(obj=None) ya muestra los inlines de
        # tarjeta y posnet con una fila para completar, y su formset
        # (_RequiredOnAddInlineFormSet) exige esa fila antes de llegar acá. Nada
        # que frenar en este método para ese caso.
        #
        # Cambiar el método de un pago que YA existe a tarjeta/QR/link en la misma
        # edición es otra historia: el inline que se mostró es el del método
        # ANTERIOR (u ninguno), no hay forma de haber cargado el detalle nuevo en
        # este mismo submit. Guardar así deja un pago sin detalle, y los reportes
        # que juntan por detalle_tarjeta/detalle_posnet lo ignoran.
        if obj.pk and obj.metodo == Payment.POSNET_TARJETA and not hasattr(obj, "detalle_tarjeta"):
            from django.contrib import messages

            messages.error(
                request,
                "Un pago con método Tarjeta necesita su detalle de tarjeta. "
                "Cargalo desde 'Crear pago' en el sistema del operador.",
            )
            return

        if (
            obj.pk
            and obj.metodo in (Payment.POSNET_QR, Payment.LINK_PAGO)
            and not hasattr(obj, "detalle_posnet")
        ):
            from django.contrib import messages

            messages.error(
                request,
                f"Un pago con método {obj.get_metodo_display()} necesita su detalle de "
                "posnet. Cargalo desde 'Crear pago' en el sistema del operador.",
            )
            return

        # Validate business rules before saving
        try:
            obj.clean()
        except ValidationError as e:
            from django.contrib import messages

            for field, errors in e.message_dict.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            return

        # Two-phase save to ensure filename uses Payment.id
        uploaded_file = getattr(obj, "proof_of_payment_file", None)
        if not change and uploaded_file:
            # Temporarily remove file, save to get PK, then assign file and save again
            obj.proof_of_payment_file = None
            obj.save()
            obj.proof_of_payment_file = uploaded_file
            obj.save(update_fields=["proof_of_payment_file"])
        else:
            obj.save()

    def get_readonly_fields(self, request, obj=None):
        if self._user_may_only_mark_reviewed(request):
            return [f.name for f in self.model._meta.fields if f.name not in ("reviewed", "id")] + [
                "preview_proof"
            ]
        return list(self.readonly_fields)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Show all active recipients (both REGULAR and DEPOSIT_ONLY)
        if "payment_recipient" in form.base_fields:
            form.base_fields["payment_recipient"].queryset = PaymentRecipient.objects.filter(
                is_active=True
            ).order_by("priority_order", "name")

        # Filter professionals to show only users with professional role
        if "professional" in form.base_fields:
            form.base_fields["professional"].queryset = User.objects.filter(
                role=User.PROFESSIONAL, is_active=True
            ).order_by("username")

        # Set operator_user to current user for new payments
        if "operator_user" in form.base_fields and not obj:
            form.base_fields["operator_user"].initial = request.user
            if hasattr(request.user, "role") and request.user.role == User.OPERATOR:
                form.base_fields["operator_user"].widget.attrs["readonly"] = True

        if "amount" in form.base_fields:
            form.base_fields["amount"].widget.attrs["style"] = "width: 10em;"

        is_admin = request.user.is_superuser or (
            hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR
        )

        # Se parchea el método en la clase devuelta por super().get_form(), no se
        # subclasea: subclasear dispara el metaclass de ModelForm y reconstruye
        # base_fields desde Meta.model, perdiendo los querysets custom de arriba.
        original_post_clean = form._post_clean

        def _post_clean_with_admin_bypass(self):
            # El administrador corrige/carga datos a mano: ninguna regla de negocio
            # de Payment.clean() lo bloquea acá (ver models.py:Payment.clean).
            self.instance._admin_bypass_validation = is_admin
            original_post_clean(self)

        form._post_clean = _post_clean_with_admin_bypass
        return form

    def get_inline_instances(self, request, obj=None):
        if obj is not None:
            # Editando un pago que ya existe: el inline que corresponde a su método,
            # con la fila del detalle ya cargada. Con otro método no hay detalle que
            # editar.
            if obj.metodo == Payment.POSNET_TARJETA:
                return [TarjetaPaymentDetailInline(self.model, self.admin_site)]
            if obj.metodo in (Payment.POSNET_QR, Payment.LINK_PAGO):
                return [PosnetPaymentDetailInline(self.model, self.admin_site)]
            return []
        # Alta nueva: todavía no se sabe qué método va a elegir el administrador
        # (recién se sabe al enviar el formulario), así que se muestran los dos
        # inlines, sin ninguna fila por defecto — el "Agregar otro" nativo del
        # admin los despliega. El formset de cada uno (ver
        # _RequiredOnAddInlineFormSet) exige completar una fila solo si el
        # método elegido lo necesita; el otro se ignora si queda sin usar.
        return [
            TarjetaPaymentDetailInlineOnAdd(self.model, self.admin_site),
            PosnetPaymentDetailInlineOnAdd(self.model, self.admin_site),
        ]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Administrators see all payments
        if hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR:
            return qs
        # Professionals see only their assigned payments
        elif hasattr(request.user, "role") and request.user.role == User.PROFESSIONAL:
            return qs.filter(professional=request.user)
        elif hasattr(request.user, "role") and request.user.role == User.OPERATOR:
            return _filter_queryset_for_operator_payments(qs, request.user)
        else:
            return qs.filter(operator_user=request.user)

    def has_add_permission(self, request):
        # Only administrators and operators can add payments, professionals cannot
        if request.user.is_superuser:
            return True
        if hasattr(request.user, "role"):
            return request.user.role in [User.ADMINISTRATOR]
        return False

    def has_change_permission(self, request, obj=None):
        if self._can_edit_payment_fully(request):
            return True
        if self._can_mark_reviewed(request):
            return True
        return False

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        # Only administrators can delete payments
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        # Allow administrators, operators, and professionals to access Payment admin
        if hasattr(request.user, "role"):
            return request.user.role in [User.ADMINISTRATOR, User.OPERATOR, User.PROFESSIONAL]
        return False

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        # Allow administrators, operators, and professionals to view payments
        if hasattr(request.user, "role"):
            return request.user.role in [User.ADMINISTRATOR, User.OPERATOR, User.PROFESSIONAL]
        return False

    def download_payments_csv(self, request, queryset):
        """Export selected payments as CSV"""
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="pagos-{timezone.localtime(timezone.now()).strftime("%d-%m-%Y")}.csv"'
        )
        response.write("\ufeff")

        writer = csv.writer(response)
        writer.writerow(
            [
                "ID",
                "Fecha",
                "Método",
                "Monto",
                "Destinatario",
                "Alias",
                "CBU",
                "Profesional",
                "Nombre del paciente",
                "Creado por",
                "Link al comprobante",
                "Observaciones",
            ]
        )

        for payment in queryset.select_related(
            "payment_recipient", "professional", "operator_user"
        ):
            recipient = payment.payment_recipient
            writer.writerow(
                [
                    payment.id,
                    timezone.localtime(payment.created_at).strftime("%d/%m/%Y %H:%M:%S"),
                    payment.get_metodo_display(),
                    payment.amount,
                    recipient.name if recipient else "",
                    (recipient.alias or "") if recipient else "",
                    (recipient.cbu or "") if recipient else "",
                    f"{payment.professional.first_name} {payment.professional.last_name}".strip()
                    or payment.professional.username,
                    payment.patient_name,
                    f"{payment.operator_user.first_name} {payment.operator_user.last_name}".strip()
                    or payment.operator_user.username,
                    f"{payment.proof_of_payment_file.url}" if payment.proof_of_payment_file else "",
                    payment.observaciones or "",
                ]
            )

        self.message_user(request, f"Se exportaron {queryset.count()} pagos a CSV.")
        return response

    download_payments_csv.short_description = "Descargar pagos seleccionados como CSV"

    def download_proof_files(self, request, queryset):
        """Download all proof of payment files in a compressed ZIP file"""
        from django.utils.text import slugify

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            file_count = 0
            for payment in queryset.select_related("payment_recipient"):
                if payment.proof_of_payment_file:
                    try:
                        file_path = payment.proof_of_payment_file.path
                        if os.path.exists(file_path):
                            # prepare values
                            payment_id = payment.id
                            alias = getattr(payment.payment_recipient, "alias", "") or ""
                            alias_slug = slugify(alias) if alias else "sin_alias"
                            # Fecha de creación en formato día_mes_año_hora_minutos
                            fecha = timezone.localtime(payment.created_at)
                            fecha_str = fecha.strftime("%d_%m_%Y_%H_%M")
                            # Use file extension from original file
                            ext = os.path.splitext(file_path)[1]
                            # Compose filename
                            zip_filename = f"{payment_id}_{alias_slug}_{fecha_str}{ext}"
                            zip_file.write(file_path, zip_filename)
                            file_count += 1
                    except Exception:
                        pass

        if file_count == 0:
            self.message_user(
                request,
                "No se encontraron comprobantes en los pagos seleccionados.",
                level="warning",
            )
            return

        zip_buffer.seek(0)
        response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
        filename = f"comprobantes-{timezone.localtime(timezone.now()).strftime('%d-%m-%Y')}.zip"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        self.message_user(request, f"Se descargaron {file_count} comprobantes.")
        return response

    download_proof_files.short_description = "Descargar comprobantes seleccionados"

    def copy_payments_to_clipboard(self, request, queryset):
        """Format selected payments for clipboard copy"""
        from django.shortcuts import render

        payments = list(queryset.select_related("payment_recipient").order_by("created_at"))
        formatted_payments = []
        total_amount = 0
        for idx, payment in enumerate(payments, start=1):
            fecha = timezone.localtime(payment.created_at)
            fecha_str = fecha.strftime("%d/%m/%Y %H:%M")
            amount_str = f"${format_money_thousand_separator(payment.amount)}"
            if payment.payment_recipient:
                alias = (payment.payment_recipient.alias or payment.payment_recipient.name).lower()
            else:
                alias = payment.get_metodo_display().lower()
            patient_name = payment.patient_name.title()

            total_amount += payment.amount
            formatted_payments.append(
                f"{idx}. {fecha_str} - {amount_str} - {alias} - {patient_name}"
            )

        formatted_text = "\n".join(formatted_payments)
        if formatted_payments:
            formatted_text += f"\n\nTotal ${format_money_thousand_separator(total_amount)}"

        return render(
            request,
            "admin/payment_instructions/copy_payments.html",
            {
                "formatted_text": formatted_text,
                "count": queryset.count(),
                "title": "Copiar pagos al portapapeles",
            },
        )

    copy_payments_to_clipboard.short_description = "Copiar pagos seleccionados al portapapeles"

    def cancelar_pagos(self, request, queryset):
        """Cancela en masa los pagos PENDIENTES seleccionados.

        Solo pendientes, a propósito: un COMPLETADO es plata que ya se movió, y
        anularlo de a montones sin confirmación saca los importes de los totales del
        mes, de las estadísticas del profesional y del cupo consumido, sin que nadie
        haya decidido eso pago por pago. Para anular uno cobrado se lo edita a mano.

        Motivo fijo ERROR_DE_CARGA: la acción masiva no tiene dónde preguntarlo, y es
        el único que tiene sentido para una corrección hecha desde el admin.

        Todo en una transacción: si una cancelación falla a mitad de la tanda, no
        queda media tanda cancelada y media no."""
        pendientes = queryset.filter(estado=Payment.PENDIENTE)
        ignorados = queryset.count() - pendientes.count()

        cancelados = 0
        with transaction.atomic():
            for payment in pendientes:
                payment.cancelar(
                    motivo=Payment.ERROR_DE_CARGA,
                    usuario=request.user,
                    observacion="Cancelado en masa desde el administrador.",
                )
                cancelados += 1

        mensaje = f"{cancelados} pagos cancelados. El cupo quedó liberado."
        if ignorados:
            mensaje += f" Se ignoraron {ignorados} que no estaban pendientes."
        self.message_user(request, mensaje)

    cancelar_pagos.short_description = "Cancelar pagos pendientes seleccionados (libera el cupo)"

    actions = [
        "download_payments_csv",
        "download_proof_files",
        "copy_payments_to_clipboard",
        "cancelar_pagos",
    ]

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Restrict custom bulk actions to superusers and administrators only.
        if request.user.is_superuser:
            return actions
        if hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR:
            return actions

        for action_name in [
            "download_payments_csv",
            "download_proof_files",
            "copy_payments_to_clipboard",
            "cancelar_pagos",
        ]:
            actions.pop(action_name, None)
        return actions

    def _can_view_payments_subtotal(self, request):
        if request.user.is_superuser:
            return True
        return getattr(request.user, "role", None) == User.ADMINISTRATOR

    def changelist_view(self, request, extra_context=None):
        from django.urls import reverse

        self._changelist_request = request
        extra_context = extra_context or {}
        if self._can_toggle_reviewed(request):
            extra_context["payment_toggle_reviewed_url"] = reverse(
                "admin:payment_instructions_payment_toggle_reviewed",
            )
        old_date_hierarchy = self.date_hierarchy
        if getattr(request.user, "role", None) == User.OPERATOR:
            self.date_hierarchy = None
        try:
            response = super().changelist_view(request, extra_context=extra_context)
        finally:
            self.date_hierarchy = old_date_hierarchy
        if not self._can_view_payments_subtotal(request):
            return response
        try:
            cl = response.context_data.get("cl") if hasattr(response, "context_data") else None
        except Exception:
            cl = None
        if cl is not None:
            qs = getattr(cl, "queryset", self.get_queryset(request))
            subtotal_amount = qs.aggregate(total=models.Sum("amount"))["total"] or 0
            subtotal_display = f"${format_money_thousand_separator(subtotal_amount)}"
            response.context_data["subtotal_amount"] = subtotal_amount
            response.context_data["subtotal_display"] = subtotal_display
        return response


@admin.register(ProfessionalPaymentStats)
class ProfessionalPaymentStatsAdmin(admin.ModelAdmin):
    list_display = ("full_name", "current_month_amount_display", "current_month_count_display")
    search_fields = ("username", "first_name", "last_name")
    ordering = ("username",)
    date_hierarchy = "professional_payments__created_at"

    # Show inside the professional the total historic amount
    fieldsets = (
        (
            "Información básica",
            {
                "fields": (
                    "full_name",
                    "total_payments_amount_display",
                    "total_payments_count_display",
                )
            },
        ),
    )

    # Join in one field first_name and last_name
    def full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"

    full_name.short_description = "Nombre completo"

    # Make all fields read-only since this is for statistics only
    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False  # Cannot add through this interface

    def has_delete_permission(self, request, obj=None):
        return False  # Cannot delete through this interface

    def has_change_permission(self, request, obj=None):
        return False  # Read-only interface

    def get_queryset(self, request):
        # Only show professionals
        qs = super().get_queryset(request)
        return qs.filter(role=User.PROFESSIONAL)

    def total_payments_amount_display(self, obj):
        amount = obj.get_total_payments_amount()
        return f"${format_money_thousand_separator(amount)}"

    total_payments_amount_display.short_description = "Historico Total de Pagos ($)"
    total_payments_amount_display.admin_order_field = "professional_payments__amount"

    def total_payments_count_display(self, obj):
        count = obj.get_total_payments_count()
        return f"{count}"

    total_payments_count_display.short_description = "Historico Total de Cantidad de Pagos (#)"

    def current_month_amount_display(self, obj):
        request = getattr(self, "request_for_date_hierarchy", None)
        year, month = None, None
        if request:
            year = request.GET.get("professional_payments__created_at__year")
            month = request.GET.get("professional_payments__created_at__month")
            if year:
                year = int(year)
            if month:
                month = int(month)
        amount = obj.get_current_month_payments_amount(year=year, month=month)
        return f"${format_money_thousand_separator(amount)}"

    current_month_amount_display.short_description = "Monto generado en el período ($)"

    def current_month_count_display(self, obj):
        request = getattr(self, "request_for_date_hierarchy", None)
        year, month = None, None
        if request:
            year = request.GET.get("professional_payments__created_at__year")
            month = request.GET.get("professional_payments__created_at__month")
            if year:
                year = int(year)
            if month:
                month = int(month)
        count = obj.get_current_month_payments_count(year=year, month=month)
        return f"{count}"

    current_month_count_display.short_description = "Cantidad de pagos en el período (#)"

    def changelist_view(self, request, extra_context=None):
        # Store request for use in display methods
        self.request_for_date_hierarchy = request
        return super().changelist_view(request, extra_context)

    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        # Only administrators can access this module
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        # Only administrators can view this
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR


@admin.register(RecipientPaymentStats)
class RecipientPaymentStatsAdmin(admin.ModelAdmin):
    list_display = ("name", "alias", "period_amount_display", "period_count_display")
    search_fields = ("name", "alias", "cbu")
    ordering = ("name",)
    date_hierarchy = "payments__created_at"

    # Show inside the recipient the total historic amount
    fieldsets = (
        (
            "Información básica",
            {
                "fields": (
                    "name",
                    "alias",
                    "total_payments_amount_display",
                    "total_payments_count_display",
                )
            },
        ),
    )

    # Make all fields read-only since this is for statistics only
    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False  # Cannot add through this interface

    def has_delete_permission(self, request, obj=None):
        return False  # Cannot delete through this interface

    def has_change_permission(self, request, obj=None):
        return False  # Read-only interface

    def get_queryset(self, request):
        # Show all recipients
        qs = super().get_queryset(request)
        return qs

    def total_payments_amount_display(self, obj):
        amount = obj.get_total_payments_amount()
        return f"${format_money_thousand_separator(amount)}"

    total_payments_amount_display.short_description = "Histórico Total de Pagos ($)"

    def total_payments_count_display(self, obj):
        count = obj.get_total_payments_count()
        return f"{count}"

    total_payments_count_display.short_description = "Histórico Total de Cantidad de Pagos (#)"

    def period_amount_display(self, obj):
        request = getattr(self, "request_for_date_hierarchy", None)
        year, month, day = None, None, None
        if request:
            year = request.GET.get("payments__created_at__year")
            month = request.GET.get("payments__created_at__month")
            day = request.GET.get("payments__created_at__day")
            if year:
                year = int(year)
            if month:
                month = int(month)
            if day:
                day = int(day)
        amount = obj.get_period_payments_amount(year=year, month=month, day=day)
        return f"${format_money_thousand_separator(amount)}"

    period_amount_display.short_description = "Monto cobrado en el período ($)"

    def period_count_display(self, obj):
        request = getattr(self, "request_for_date_hierarchy", None)
        year, month, day = None, None, None
        if request:
            year = request.GET.get("payments__created_at__year")
            month = request.GET.get("payments__created_at__month")
            day = request.GET.get("payments__created_at__day")
            if year:
                year = int(year)
            if month:
                month = int(month)
            if day:
                day = int(day)
        count = obj.get_period_payments_count(year=year, month=month, day=day)
        return f"{count}"

    period_count_display.short_description = "Cantidad de pagos en el período (#)"

    def changelist_view(self, request, extra_context=None):
        # Store request for use in display methods
        self.request_for_date_hierarchy = request
        return super().changelist_view(request, extra_context)

    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        # Only administrators can access this module
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        # Only administrators can view this
        return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR


# Customize admin site headers
admin.site.site_header = "Docta Dent - Clinica dental"
admin.site.site_title = "Sistema de Instrucciones de pago"


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "rule_type",
        "trigger_weekday",
        "trigger_monthday",
        "action_type",
        "is_active",
        "last_executed",
    )
    list_filter = ("is_active", "rule_type", "action_type")
    search_fields = ("name", "payment_recipient__name", "payment_recipient__alias")
    readonly_fields = ("last_executed", "created_at", "updated_at", "rule_preview")

    fieldsets = (
        ("Información básica", {"fields": ("name", "payment_recipient", "is_active")}),
        (
            "Configuración del disparador",
            {"fields": ("rule_type", "trigger_weekday", "trigger_monthday", "trigger_time")},
        ),
        ("Configuración de tipo", {"fields": ("action_type",)}),
        (
            "Configuración de valor",
            {"fields": ("priority_position", "priority_value", "active_value")},
        ),
        ("Estado de ejecución", {"fields": ("last_executed", "created_at", "updated_at")}),
    )

    actions = ["activate_rules", "deactivate_rules", "test_rule"]

    def activate_rules(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Activated {updated} rules.")

    def deactivate_rules(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Deactivated {updated} rules.")

    def rule_preview(self, obj):
        if not obj:
            return ""
        if obj.action_type == AutomationRule.ActionType.SET_ACTIVE_STATUS:
            return f"Set active to {obj.active_value} for {obj.payment_recipient.alias}"
        if obj.action_type == AutomationRule.ActionType.SET_PRIORITY:
            if obj.priority_position == AutomationRule.PriorityPosition.SPECIFIC:
                return f"Set priority to {obj.priority_value} for {obj.payment_recipient.alias}"
            return (
                f"Set priority position {obj.priority_position} for {obj.payment_recipient.alias}"
            )
        return ""

    rule_preview.short_description = "Vista previa"

    def save_model(self, request, obj, form, change):
        try:
            obj.clean()
        except ValidationError as e:
            from django.contrib import messages

            for field, errors in e.message_dict.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            return
        super().save_model(request, obj, form, change)

    def test_rule(self, request, queryset):
        count = 0
        for rule in queryset:
            try:
                execute_single_rule(rule.id)
                count += 1
            except Exception as e:
                from django.contrib import messages

                messages.error(request, f"Error testing rule {rule.name}: {e}")
        self.message_user(request, f"Executed {count} rules for test.")

    test_rule.short_description = "Probar ejecución de regla(s) ahora"


@admin.register(RuleExecutionLog)
class RuleExecutionLogAdmin(admin.ModelAdmin):
    list_display = ("rule", "executed_at", "success")
    list_filter = ("success", "executed_at", "rule__rule_type", "rule__action_type")
    search_fields = ("rule__name", "payment_recipient__name", "action_taken")
    readonly_fields = [f.name for f in RuleExecutionLog._meta.fields]
    date_hierarchy = "executed_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


def _is_administrator(request):
    if request.user.is_superuser:
        return True
    return hasattr(request.user, "role") and request.user.role == User.ADMINISTRATOR


@admin.register(Banco)
class BancoAdmin(admin.ModelAdmin):
    """Backoffice exclusivo del administrador: el operador solo elige el banco
    por nombre desde su propio formulario, nunca entra acá."""

    list_display = ("nombre", "is_active")
    list_filter = ("is_active",)
    search_fields = ("nombre",)

    def get_queryset(self, request):
        return super().get_queryset(request)

    def has_add_permission(self, request):
        return _is_administrator(request)

    def has_change_permission(self, request, obj=None):
        return _is_administrator(request)

    def has_delete_permission(self, request, obj=None):
        return _is_administrator(request)

    def has_module_permission(self, request):
        return _is_administrator(request)


@admin.register(Posnet)
class PosnetAdmin(admin.ModelAdmin):
    """Backoffice exclusivo del administrador: el operador solo elige el posnet
    por nombre desde su propio formulario, nunca entra acá."""

    list_display = (
        "nombre",
        "cuenta_actual",
        "monto_cobrado_cuenta_actual_display",
        "cuenta_actual_desde",
        "acepta_credito",
        "acepta_debito",
        "acepta_qr",
        "acepta_link_pago",
    )
    list_filter = (
        "proveedor",
        "is_active",
        "acepta_credito",
        "acepta_debito",
        "acepta_qr",
        "acepta_link_pago",
    )
    search_fields = ("nombre", "cuenta_actual__nombre")
    list_select_related = ("cuenta_actual",)

    @admin.display(description="cobrado")
    def monto_cobrado_cuenta_actual_display(self, obj):
        return f"${format_money_thousand_separator(obj.get_monto_cobrado_cuenta_actual())}"

    def get_queryset(self, request):
        return super().get_queryset(request)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "cuenta_actual" in form.base_fields:
            form.base_fields["cuenta_actual"].queryset = CuentaPosnet.objects.filter(
                is_active=True
            ).order_by("nombre")
        return form

    def has_add_permission(self, request):
        return _is_administrator(request)

    def has_change_permission(self, request, obj=None):
        return _is_administrator(request)

    def has_delete_permission(self, request, obj=None):
        return _is_administrator(request)

    def has_module_permission(self, request):
        return _is_administrator(request)


@admin.register(PosnetAccountPaymentStats)
class PosnetAccountPaymentStatsAdmin(admin.ModelAdmin):
    """Backoffice exclusivo del administrador: cuánto generó cada cuenta de
    posnet en un período. Mismo patrón que RecipientPaymentStatsAdmin."""

    list_display = ("nombre", "period_amount_display", "period_count_display")
    search_fields = ("nombre",)
    ordering = ("nombre",)
    date_hierarchy = "pagos_tarjeta__payment__created_at"

    fieldsets = (
        (
            "Información básica",
            {"fields": ("nombre", "total_payments_amount_display", "total_payments_count_display")},
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_module_permission(self, request):
        return _is_administrator(request)

    def has_view_permission(self, request, obj=None):
        return _is_administrator(request)

    def get_queryset(self, request):
        return super().get_queryset(request)

    @admin.display(description="Histórico Total de Pagos ($)")
    def total_payments_amount_display(self, obj):
        return f"${format_money_thousand_separator(obj.get_total_payments_amount())}"

    @admin.display(description="Histórico Total de Cantidad de Pagos (#)")
    def total_payments_count_display(self, obj):
        return f"{obj.get_total_payments_count()}"

    @admin.display(description="Monto generado en el período ($)")
    def period_amount_display(self, obj):
        year, month, day = self._period_from_request()
        amount = obj.get_period_payments_amount(year=year, month=month, day=day)
        return f"${format_money_thousand_separator(amount)}"

    @admin.display(description="Cantidad de pagos en el período (#)")
    def period_count_display(self, obj):
        year, month, day = self._period_from_request()
        return f"{obj.get_period_payments_count(year=year, month=month, day=day)}"

    def _period_from_request(self):
        request = getattr(self, "request_for_date_hierarchy", None)
        if not request:
            return None, None, None
        prefix = "pagos_tarjeta__payment__created_at"
        year = request.GET.get(f"{prefix}__year")
        month = request.GET.get(f"{prefix}__month")
        day = request.GET.get(f"{prefix}__day")
        return (
            int(year) if year else None,
            int(month) if month else None,
            int(day) if day else None,
        )

    def changelist_view(self, request, extra_context=None):
        self.request_for_date_hierarchy = request
        return super().changelist_view(request, extra_context)


@admin.register(CuentaPosnet)
class CuentaPosnetAdmin(admin.ModelAdmin):
    """Backoffice exclusivo del administrador: el operador solo elige la cuenta
    por nombre desde su propio formulario, nunca entra acá."""

    list_display = (
        "nombre",
        "posnet_actual_display",
        "objetivo_display",
        "acumulado_display",
        "porcentaje_display",
        "acumulado_desde",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("nombre",)
    actions = ["reiniciar_acumulado"]

    def get_queryset(self, request):
        return super().get_queryset(request)

    @admin.display(description="Logeada en")
    def posnet_actual_display(self, obj):
        nombres = obj.posnets.values_list("nombre", flat=True)
        return ", ".join(nombres) if nombres else "—"

    @admin.display(description="Objetivo")
    def objetivo_display(self, obj):
        if not obj.objetivo_monto:
            return "Sin objetivo"
        return f"${format_money_thousand_separator(obj.objetivo_monto)}"

    @admin.display(description="Acumulado")
    def acumulado_display(self, obj):
        return f"${format_money_thousand_separator(obj.get_acumulado_recibido())}"

    @admin.display(description="% del objetivo")
    def porcentaje_display(self, obj):
        pct = obj.get_porcentaje_objetivo()
        if pct is None:
            return "—"
        return f"{pct}%"

    def reiniciar_acumulado(self, request, queryset):
        count = queryset.count()
        queryset.update(acumulado_desde=timezone.now())
        self.message_user(request, f"Acumulado reiniciado para {count} cuenta(s).")

    reiniciar_acumulado.short_description = "Reiniciar acumulado desde hoy"

    def has_add_permission(self, request):
        return _is_administrator(request)

    def has_change_permission(self, request, obj=None):
        return _is_administrator(request)

    def has_delete_permission(self, request, obj=None):
        return _is_administrator(request)

    def has_module_permission(self, request):
        return _is_administrator(request)


# Agrupa el índice del admin en secciones en vez de una sola lista plana.
# Todos los modelos viven en una única app (payment_instructions), así que
# Django no tiene con qué separarlos por sí solo.
ADMIN_APP_GROUPS = {
    "Principal": [
        "Payment",
        "PaymentRecipient",
        "DepositRecipient",
        "CuentaPosnet",
        "Posnet",
    ],
    "Estadísticas": [
        "ProfessionalPaymentStats",
        "RecipientPaymentStats",
        "PosnetAccountPaymentStats",
    ],
    "Otros": ["Banco", "AutomationRule", "RuleExecutionLog"],
    "Usuarios": ["User"],
}


# Único app_label real registrado ante Django. Django solo genera una URL
# de app_index por app_label existente en el registro, así que los grupos
# sintéticos no pueden tener cada uno su propia URL: todos apuntan a esta.
REAL_APP_LABEL = "payment_instructions"


def _grouped_get_app_list(self, request, app_label=None):
    app_dict = self._build_app_dict(request)
    models_by_name = {
        model["object_name"]: model for app in app_dict.values() for model in app["models"]
    }
    real_app_url = reverse("admin:app_list", kwargs={"app_label": REAL_APP_LABEL})

    grouped = []
    assigned = set()
    for group_name, model_names in ADMIN_APP_GROUPS.items():
        group_models = [models_by_name[name] for name in model_names if name in models_by_name]
        assigned.update(model_names)
        if not group_models:
            continue
        grouped.append(
            {
                "name": group_name,
                "app_label": slugify(group_name),
                "app_url": real_app_url,
                "has_module_perms": True,
                "models": group_models,
            }
        )

    remaining = [m for name, m in models_by_name.items() if name not in assigned]
    if remaining:
        remaining.sort(key=lambda m: m["name"])
        grouped.append(
            {
                "name": "Otros",
                "app_label": "otros",
                "app_url": real_app_url,
                "has_module_perms": True,
                "models": remaining,
            }
        )

    # app_label solo llega acá con el valor real (breadcrumbs de changelist)
    # o con un slug de grupo (llamada directa). El real muestra todo junto.
    if app_label and app_label != REAL_APP_LABEL:
        grouped = [g for g in grouped if g["app_label"] == app_label]
    return grouped


admin.site.get_app_list = types.MethodType(_grouped_get_app_list, admin.site)


# Hide django-celery-beat models from admin menu and
try:
    from django_celery_beat.models import (
        PeriodicTask,
        CrontabSchedule,
        IntervalSchedule,
        SolarSchedule,
        ClockedSchedule,
    )

    from django.contrib.auth.models import Group

    for _model in [
        PeriodicTask,
        CrontabSchedule,
        IntervalSchedule,
        SolarSchedule,
        ClockedSchedule,
        Group,
    ]:
        try:
            admin.site.unregister(_model)
        except admin.sites.NotRegistered:
            pass
except Exception:
    # If beat is not installed or any import fails, just ignore
    pass
