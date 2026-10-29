from django.db import models
from django.db.models import Q

from sistema.core.models import Registro


class Copia(Registro):
    """Copia local de una lista de Dentalink (FR-005). Nunca se borra: pasa a no vigente."""

    id_dentalink = models.PositiveIntegerField(unique=True)
    nombre = models.CharField(max_length=120)
    vigente = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Banco(Copia):
    class Meta(Copia.Meta):
        verbose_name = "banco"


class Profesional(Copia):
    class Meta(Copia.Meta):
        verbose_name = "profesional"


class MedioPago(Copia):
    class Meta(Copia.Meta):
        verbose_name = "medio de pago"


class ConfiguracionDentalink(Registro):
    id_prestacion_saldo_a_favor = models.PositiveIntegerField(null=True, blank=True)
    nombre_prestacion_saldo_a_favor = models.CharField(max_length=120, blank=True)
    banco_por_defecto = models.ForeignKey(
        Banco, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(id_prestacion_saldo_a_favor__isnull=True)
                    & Q(nombre_prestacion_saldo_a_favor="")
                )
                | (
                    Q(id_prestacion_saldo_a_favor__isnull=False)
                    & ~Q(nombre_prestacion_saldo_a_favor="")
                ),
                name="config_saldo_a_favor_completo",
                violation_error_message="Cargá el id y el nombre de la prestación saldo a favor.",
            ),
            models.CheckConstraint(
                condition=Q(pk=1),
                name="config_dentalink_unica",
                violation_error_message="Hay una sola configuración de Dentalink.",
            ),
        ]

    @classmethod
    def actual(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def __str__(self):
        return "Configuración de Dentalink"
