from django import forms

# %% porque Django formatea el mensaje con %.
_MARGEN = "El margen debe ser de 0 %% a 99 %%."
_MARGEN_PLANO = "El margen debe ser de 0 % a 99 %."


class MargenForm(forms.Form):
    margen = forms.IntegerField(
        min_value=0,
        max_value=99,
        widget=forms.NumberInput(attrs={"class": "campo-corto"}),
        error_messages={
            "min_value": _MARGEN,
            "max_value": _MARGEN,
            "invalid": _MARGEN_PLANO,
            "required": _MARGEN_PLANO,
        },
    )


class DireccionForm(forms.Form):
    # Sin validar acá: el servicio valida y da el mensaje.
    email = forms.CharField(max_length=254)
