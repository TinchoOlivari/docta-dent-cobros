from django import forms
from django.db.models import Q

from sistema.destinatarios.models import Cuenta

from .models import Regla

DIAS_SEMANA = [
    (1, "Lunes"), (2, "Martes"), (3, "Miércoles"), (4, "Jueves"),
    (5, "Viernes"), (6, "Sábado"), (7, "Domingo"),
]  # fmt: skip


MENSAJE_DIA = "El día debe ir de 1 a 31."
MAXIMO_POSICION = 2147483647  # tope de PositiveIntegerField


class ReglaForm(forms.ModelForm):
    dia_semana = forms.ChoiceField(choices=DIAS_SEMANA, required=False, label="Día de la semana")
    dia_mes = forms.IntegerField(
        required=False,
        label="Día del mes",
        min_value=1,
        max_value=31,
        error_messages={
            "invalid": MENSAJE_DIA,
            "min_value": MENSAJE_DIA,
            "max_value": MENSAJE_DIA,
        },
    )

    class Meta:
        model = Regla
        fields = ["nombre", "cuenta", "estado", "disparador", "hora", "accion", "posicion"]
        widgets = {
            "hora": forms.TimeInput(
                format="%H:%M",
                attrs={"placeholder": "HH:MM", "inputmode": "numeric", "class": "campo-corto"},
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # FR-033: ni destinatarios ni cuentas Archivados (salvo la cuenta que la regla ya tiene)
        visibles = Q(destinatario__estado__in=["activo", "inactivo"]) & ~Q(estado="archivada")
        if self.instance.cuenta_id:
            visibles |= Q(pk=self.instance.cuenta_id)
        self.fields["cuenta"].queryset = (
            Cuenta.objects.filter(visibles, tipo="transferencia")
            .select_related("destinatario")
            .para_selector()
        )
        self.fields["hora"].error_messages["invalid"] = "Escribí la hora como HH:MM."
        self.fields["accion"].choices = [
            (v, Regla.TEXTO_ACCION.get(v, t)) for v, t in self.fields["accion"].choices
        ]
        # FR-044: la posición va de 1 a la cantidad de cuentas de la lista
        self.cuentas_en_lista = Cuenta.objects.filter(posicion__isnull=False).count()
        self.fields["posicion"] = forms.IntegerField(
            required=False,
            label="Posición",
            error_messages={"invalid": self._mensaje_posicion()},
        )
        self.fields["cuenta"].label_from_instance = lambda c: c.etiqueta_selector
        if self.instance.pk:
            del self.fields["estado"]
            if self.instance.disparador == Regla.Disparador.SEMANA:
                self.initial["dia_semana"] = self.instance.dia
            else:
                self.initial["dia_mes"] = self.instance.dia

    def _mensaje_posicion(self):
        n = self.cuentas_en_lista
        return f"La posición debe ir de 1 a {n}." if n else "La posición debe ser 1 o más."

    def _update_errors(self, errors):
        # un solo mensaje por campo: el de clean_posicion gana al del modelo (FR-044)
        super()._update_errors(errors)
        if "posicion" in self._errors:
            del self._errors["posicion"][1:]

    def clean_posicion(self):
        valor = self.cleaned_data.get("posicion")
        if valor is not None and not 1 <= valor <= (self.cuentas_en_lista or MAXIMO_POSICION):
            raise forms.ValidationError(self._mensaje_posicion())
        return valor

    def clean(self):
        datos = super().clean()
        if datos.get("accion") != Regla.Accion.POSICION:
            datos["posicion"] = None
            self.errors.pop("posicion", None)
        campo = "dia_semana" if datos.get("disparador") == Regla.Disparador.SEMANA else "dia_mes"
        dia = datos.get(campo)
        if campo in self.errors:
            pass  # ya tiene su mensaje (FR-044)
        elif datos.get("disparador") and dia in (None, ""):
            self.add_error(campo, "Elegí el día.")
        elif dia not in (None, ""):
            self.instance.dia = int(dia)
        return datos

    def datos_servicio(self):
        """Campos del modelo listos para crear_regla / editar_regla."""
        datos = {k: v for k, v in self.cleaned_data.items() if k not in ("dia_semana", "dia_mes")}
        datos["dia"] = self.instance.dia
        return datos
