from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

MENSAJE_INMUTABLE = "Los eventos no se editan ni se borran."


class EventoQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionDenied(MENSAJE_INMUTABLE)

    def delete(self):
        raise PermissionDenied(MENSAJE_INMUTABLE)


class Evento(models.Model):
    class Tipo(models.TextChoices):
        ALTA = "alta", "Alta"
        CAMBIO = "cambio", "Cambio"
        BORRADO = "borrado", "Borrado"
        INICIO_SESION = "inicio_sesion", "Inicio de sesión"
        CIERRE_SESION = "cierre_sesion", "Cierre de sesión"
        LOGIN_FALLIDO = "login_fallido", "Login fallido"
        ENVIO_DENTALINK = "envio_dentalink", "Intento de envío a Dentalink"
        EJECUCION_REGLA = "ejecucion_regla", "Ejecución de regla de automatización"
        BLOQUEO = "bloqueo", "Bloqueo"
        DESBLOQUEO = "desbloqueo", "Desbloqueo"
        EMISION_ARCA = "emision_arca", "Intento de emisión en ARCA"

    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    fecha = models.DateTimeField(default=timezone.now, db_index=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    origen = models.CharField(max_length=200, blank=True)
    usuario_intentado = models.CharField(max_length=150, blank=True)
    entidad = models.CharField(max_length=100, blank=True)
    entidad_id = models.PositiveBigIntegerField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    dispositivo = models.CharField(max_length=300, blank=True)
    cambios = models.JSONField(default=list, blank=True, encoder=DjangoJSONEncoder)
    exito = models.BooleanField(null=True, blank=True)
    detalle = models.TextField(blank=True)

    objects = EventoQuerySet.as_manager()

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["entidad", "entidad_id", "-fecha"], name="evento_entidad_idx"),
            models.Index(fields=["tipo", "usuario_intentado", "-fecha"], name="evento_login_idx"),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.entidad} {self.entidad_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionDenied(MENSAJE_INMUTABLE)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied(MENSAJE_INMUTABLE)
