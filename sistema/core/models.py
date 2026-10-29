from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import ProtectedError


class MontoField(models.DecimalField):
    """Monto en pesos con centavos (principio III). Mínimo 0,00."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("validators", [MinValueValidator(Decimal("0.00"))])
        super().__init__(*args, **kwargs)

    def formfield(self, **kwargs):
        from sistema.core.forms import MontoDecimalField

        kwargs.setdefault("form_class", MontoDecimalField)
        kwargs.setdefault("min_value", Decimal("0.00"))
        return super().formfield(**kwargs)

    def deconstruct(self):
        # Los defaults viven en __init__: la migración no los repite.
        name, path, args, kwargs = super().deconstruct()
        if kwargs.get("max_digits") == 12:
            del kwargs["max_digits"]
        if kwargs.get("decimal_places") == 2:
            del kwargs["decimal_places"]
        kwargs.pop("validators", None)
        return name, path, args, kwargs


class RegistroQuerySet(models.QuerySet):
    def delete(self):
        raise ProtectedError("Nada se borra. Archivá el registro.", set(self))


class Registro(models.Model):
    fecha_alta = models.DateTimeField(auto_now_add=True)
    fecha_cambio = models.DateTimeField(auto_now=True)
    observaciones = models.TextField(blank=True)

    objects = RegistroQuerySet.as_manager()

    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        raise ProtectedError("Nada se borra. Archivá el registro.", [self])
