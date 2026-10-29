from ..models import PaymentRecipient


def validate_payment_amount(amount, recipient_alias=None):
    """
    Validate if a payment amount is feasible.
    Returns tuple of (is_valid, message, suggested_recipient)
    """
    try:
        amount = int(str(amount))
    except (ValueError, TypeError):
        return False, "Formato de monto inválido", None

    if amount <= 0:
        return False, "El monto debe ser mayor a cero", None

    if recipient_alias:
        try:
            recipient = PaymentRecipient.objects.get(alias=recipient_alias, is_active=True)
            if recipient.can_receive_amount(amount):
                return True, "Pago valido", recipient
            else:
                remaining_month = recipient.get_remaining_amount()
                remaining_week = recipient.get_remaining_weekly_amount()
                if remaining_week is not None:
                    # Weekly cap enforced
                    if remaining_week <= 0:
                        return False, "El destinatario ya alcanzó su límite semanal.", recipient
                    return (
                        False,
                        f"El destinatario solo puede recibir ${remaining_week} más esta semana",
                        recipient,
                    )
                return (
                    False,
                    f"El destinatario solo puede recibir ${remaining_month} mas este mes",
                    recipient,
                )
        except PaymentRecipient.DoesNotExist:
            return False, "Destinatario no encontrado o inactivo", None

    # Find any suitable recipient
    suggested_recipient = PaymentRecipient.objects.find_best_recipient(amount)
    if suggested_recipient:
        return (
            True,
            f"Monto valido - destinatario sugerido: {suggested_recipient.alias}",
            suggested_recipient,
        )
    else:
        return False, "No hay destinatarios disponibles para este monto", None
