from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from sistema.core.models import Registro


class Regla(Registro):
    class Estado(models.TextChoices):
        ACTIVA = "activa", "Activa"
        INACTIVA = "inactiva", "Inactiva"

    class Disparador(models.TextChoices):
        SEMANA = "semana", "Día de la semana"
        MES = "mes", "Día del mes"

    class Accion(models.TextChoices):
        PRIMERO = "primero", "Primero"
        ULTIMO = "ultimo", "Último"
        MEDIO = "medio", "Medio"
        POSICION = "posicion", "Posición específica"
        ACTIVAR = "activar", "Activar"
        DESACTIVAR = "desactivar", "Desactivar"

    nombre = models.CharField(max_length=200)
    cuenta = models.ForeignKey("destinatarios.Cuenta", models.PROTECT, related_name="reglas")
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ACTIVA)
    fecha_activacion = models.DateTimeField(null=True, blank=True)
    disparador = models.CharField(max_length=10, choices=Disparador.choices)
    dia = models.PositiveSmallIntegerField("día")
    hora = models.TimeField()
    accion = models.CharField("acción", max_length=20, choices=Accion.choices)
    posicion = models.PositiveIntegerField("posición", null=True, blank=True)
    ultima_ejecucion = models.DateTimeField("última ejecución", null=True, blank=True)

    # FR-047: los textos de la acción en pantalla (las choices no cambian: sin migración)
    TEXTO_ACCION = {
        "primero": "Mover al primer lugar",
        "ultimo": "Mover al último lugar",
        "medio": "Mover al medio",
        "posicion": "Mover a una posición",
        "activar": "Activar",
        "desactivar": "Desactivar",
    }

    @property
    def accion_texto(self):
        if self.accion == self.Accion.POSICION and self.posicion:
            return f"Mover a la posición {self.posicion}"
        return self.TEXTO_ACCION[self.accion]

    DIAS_SEMANA = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")

    @property
    def dia_texto(self):
        """«Lunes» para la semana (1 a 7), el número para el mes. Un valor fuera de rango queda tal cual."""
        if self.disparador == self.Disparador.SEMANA and 1 <= self.dia <= 7:
            return self.DIAS_SEMANA[self.dia - 1]
        return str(self.dia)

    @property
    def descripcion(self):
        """La regla en palabras (FR-031)."""
        if self.disparador == self.Disparador.SEMANA:
            cuando = self.DIAS_SEMANA[self.dia - 1].lower() if 1 <= self.dia <= 7 else self.dia
        else:
            cuando = f"día {self.dia}"
        extra = (
            " (o el último día del mes si es más corto)"
            if (self.disparador == self.Disparador.MES and self.dia > 28)
            else ""
        )
        c = f"«{self.cuenta.alias or self.cuenta}»"
        accion = {
            self.Accion.PRIMERO: f"mover {c} a la posición 1",
            self.Accion.POSICION: (
                f"mover {c} a la posición {self.posicion} (o a la última si hay menos cuentas)"
            ),
            self.Accion.MEDIO: f"mover {c} al medio de la lista",
            self.Accion.ULTIMO: f"mover {c} al final de la lista",
            self.Accion.ACTIVAR: f"activar {c}",
            self.Accion.DESACTIVAR: f"desactivar {c}",
        }[self.accion]
        return f"Cada {cuando} a las {self.hora:%H:%M}{extra}: {accion}."

    class Meta:
        ordering = ["hora", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(disparador="semana", dia__gte=1, dia__lte=7)
                    | Q(disparador="mes", dia__gte=1, dia__lte=31)
                ),
                name="regla_dia_valido",
                violation_error_message="El día debe ir de 1 a 7 para la semana y de 1 a 31 para el mes.",
            ),
            models.CheckConstraint(
                condition=(
                    Q(accion="posicion", posicion__gte=1)
                    | (~Q(accion="posicion") & Q(posicion__isnull=True))
                ),
                name="regla_posicion_valida",
                violation_error_message="La posición específica debe ser 1 o más.",
            ),
            models.UniqueConstraint(
                fields=["cuenta", "disparador", "dia", "hora"],
                condition=Q(estado="activa"),
                name="regla_activa_unica",
                violation_error_message=(
                    "Ya hay una regla Activa para esta cuenta con el mismo disparador, día y hora."
                ),
            ),
        ]

    def __str__(self):
        return self.nombre

    def clean(self):
        # El CHECK de SQL deja pasar NULL; la acción "posicion" sin número se frena acá.
        if self.accion == self.Accion.POSICION and self.posicion is None:
            raise ValidationError({"posicion": "La posición específica debe ser 1 o más."})
        if self.cuenta_id and self.cuenta.tipo != "transferencia":
            raise ValidationError(
                {"cuenta": "Las reglas actúan solo sobre cuentas de transferencia."}
            )
