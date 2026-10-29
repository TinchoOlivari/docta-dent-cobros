"""Fábricas de filas con la forma del esquema viejo (payment_instructions).

`bulk_create` salta el `save()` viejo, que reordena prioridades.
"""

from datetime import datetime

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from payment_instructions.models import (
    AutomationRule,
    Payment,
    PaymentRecipient,
    RuleExecutionLog,
    User,
)
from sistema.core.tiempo import ARG

CREADO = datetime(2026, 10, 10, 14, 5, tzinfo=ARG)


def fila(
    name,
    alias=None,
    cbu=None,
    max_amount=500000,
    weekly_cap_amount=0,
    min_threshold=0,
    priority_order=1,
    is_active=True,
    recipient_type="REGULAR",
    created_at=CREADO,
    notes="",
):
    obj = PaymentRecipient.objects.bulk_create(
        [
            PaymentRecipient(
                name=name,
                alias=alias,
                cbu=cbu,
                max_amount=max_amount,
                weekly_cap_amount=weekly_cap_amount,
                min_threshold=min_threshold,
                priority_order=priority_order,
                is_active=is_active,
                recipient_type=recipient_type,
                notes=notes,
            )
        ]
    )[0]
    # created_at es auto_now_add: se fija con update.
    PaymentRecipient.objects.filter(pk=obj.pk).update(created_at=created_at)
    obj.refresh_from_db()
    return obj


def usuario(
    username,
    role="operator",
    password="clave-prueba-1",
    is_superuser=False,
    is_active=True,
    first_name="",
    last_name="",
):
    return User.objects.create_user(
        username=username,
        email=f"{username}@ejemplo.test",
        password=password,
        role=role,
        is_superuser=is_superuser,
        is_active=is_active,
        first_name=first_name,
        last_name=last_name,
    )


def pago(
    fila,
    amount,
    operador,
    profesional,
    created_at=CREADO,
    payment_type="REGULAR",
    patient_name="Juan Paciente",
    archivo=None,
    observaciones=None,
    reviewed=False,
):
    nombre = ""
    if archivo is not None:
        nombre = default_storage.save(f"comprobantes/{archivo}", ContentFile(archivo.encode()))
    return Payment.objects.bulk_create(
        [
            Payment(
                amount=amount,
                payment_type=payment_type,
                metodo="TRANSFERENCIA",
                estado="COMPLETADO",
                payment_recipient=fila,
                professional=profesional,
                operator_user=operador,
                patient_name=patient_name,
                proof_of_payment_file=nombre,
                observaciones=observaciones,
                created_at=created_at,
                reviewed=reviewed,
            )
        ]
    )[0]


def regla(fila, **campos):
    campos.setdefault("name", "Regla de prueba")
    campos.setdefault("rule_type", "WEEKDAY")
    campos.setdefault("trigger_weekday", 0)
    campos.setdefault("action_type", "SET_ACTIVE_STATUS")
    campos.setdefault("active_value", True)
    return AutomationRule.objects.create(payment_recipient=fila, **campos)


def ejecucion(regla, executed_at=CREADO, **campos):
    campos.setdefault("action_taken", "Acción de prueba")
    obj = RuleExecutionLog.objects.create(
        rule=regla, payment_recipient=regla.payment_recipient, **campos
    )
    RuleExecutionLog.objects.filter(pk=obj.pk).update(executed_at=executed_at)
    obj.refresh_from_db()
    return obj
