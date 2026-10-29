from django.core.validators import EmailValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from sistema.core.models import Registro


class CampoEmail(models.EmailField):
    # Sin el validador por defecto: el mensaje de la spec va una sola vez.
    default_validators = []


class ConfiguracionAvisos(Registro):
    margen = models.PositiveSmallIntegerField(default=10)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(margen__lte=99),
                name="avisos_margen_0_a_99",
                violation_error_message="El margen debe ser de 0 %% a 99 %%.",
            ),
            models.CheckConstraint(
                condition=Q(pk=1),
                name="config_avisos_unica",
                violation_error_message="Hay una sola configuración de avisos.",
            ),
        ]

    def __str__(self):
        return "Configuración de avisos"

    @classmethod
    def actual(cls):
        return cls.objects.get_or_create(pk=1)[0]


class DireccionAviso(models.Model):
    # No es Registro: se borra (FR-001). El borrado deja evento.
    email = CampoEmail(
        max_length=254, validators=[EmailValidator("Escribí una dirección de email válida.")]
    )
    fecha_alta = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["email"]
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="avisos_email_unico",
                violation_error_message="La dirección ya está en la lista.",
            )
        ]

    def __str__(self):
        return self.email

    def clean_fields(self, exclude=None):
        # Antes de validar: el espacio sobrante no debe invalidar el email.
        self.email = (self.email or "").strip().lower()
        super().clean_fields(exclude=exclude)

    def clean(self):
        self.email = (self.email or "").strip().lower()
