from django.conf import settings
from django.db import models
from django.db.models import Q

from sistema.core.models import Registro


class Perfil(Registro):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="perfil"
    )
    id_profesional_dentalink = models.PositiveIntegerField(null=True, blank=True)
    nombre_profesional_dentalink = models.CharField(max_length=120, blank=True)
    fecha_activacion = models.DateTimeField(null=True, blank=True)
    fecha_desactivacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(id_profesional_dentalink__isnull=True) & Q(nombre_profesional_dentalink="")
                )
                | (Q(id_profesional_dentalink__isnull=False) & ~Q(nombre_profesional_dentalink="")),
                name="perfil_dentalink_completo",
                violation_error_message="Cargá el id y el nombre del profesional de Dentalink.",
            ),
        ]

    def __str__(self):
        return f"Perfil de {self.usuario}"
