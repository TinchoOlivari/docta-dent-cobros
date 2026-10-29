import re

from django import forms

from sistema.core.templatetags.pesos import pesos

# Mismo patrón que validarMonto (app.js). Solo dígitos ASCII; sin notación científica.
_MONTO = re.compile(r"^-?([0-9]{1,3}(\.[0-9]{3})*|[0-9]+)(,[0-9]+)?$")


class MontoDecimalField(forms.DecimalField):
    """Decimal que entiende el formato argentino sin JS: `1.500,50` y `1.500` son miles.

    Rechaza lo mismo que el JS (020/FR-001): `1.5`, `10.99`, `1,5e3` son inválidos.
    El punto solo separa miles en grupos de 3; el decimal es la coma.
    """

    # Textos de 020/FR-030 (voseo, coma decimal). Los mismos que el navegador (app.js).
    default_error_messages = {
        "invalid": "Escribí un monto válido, por ejemplo 1.500,50.",
        "max_digits": "El monto puede tener hasta 10 dígitos antes de la coma.",
        "max_whole_digits": "El monto puede tener hasta 10 dígitos antes de la coma.",
        "max_decimal_places": "Escribí hasta 2 decimales.",
    }

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("localize", True)  # coma decimal, como en todo campo de monto
        super().__init__(*args, **kwargs)
        minimo = self.min_value
        if minimo is not None and "min_value" not in (kwargs.get("error_messages") or {}):
            self.error_messages["min_value"] = f"El monto no puede ser menor que {pesos(minimo)}."

    def to_python(self, value):
        if isinstance(value, str):
            texto = value.strip()
            if texto and not _MONTO.match(texto):
                raise forms.ValidationError(self.error_messages["invalid"], code="invalid")
            value = texto.replace(".", "").replace(",", ".")
        return super().to_python(value)


MENSAJE_FECHA = "Escribí la fecha como dd/mm/aaaa."


class FechaField(forms.DateField):
    """Fecha escrita `25/10/2026` o `2026-10-25` (formato de máquina). El calendario Cally la completa."""

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", ["%d/%m/%Y", "%Y-%m-%d"])
        kwargs.setdefault("widget", forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
        super().__init__(**kwargs)
        self.error_messages["invalid"] = MENSAJE_FECHA


class MesField(forms.DateField):
    """Mes `aaaa-mm` (primer día del mes). `app.js` lo dibuja con 2 selectores (mes y año)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", ["%Y-%m"])
        kwargs.setdefault("widget", forms.DateInput(attrs={"type": "month"}, format="%Y-%m"))
        super().__init__(**kwargs)
        self.error_messages["invalid"] = "Elegí el mes y el año."


class _FechaHoraWidget(forms.SplitDateTimeWidget):
    def __init__(self):
        super().__init__(
            date_attrs={"type": "date"},
            time_attrs={"placeholder": "HH:MM", "inputmode": "numeric", "class": "campo-corto"},
            date_format="%Y-%m-%d",
            time_format="%H:%M",
        )


class FechaHoraField(forms.SplitDateTimeField):
    """Fecha (Cally) y hora `HH:MM` aparte. Valor: `datetime` sin zona; la vista lo localiza."""

    widget = _FechaHoraWidget

    def __init__(self, **kwargs):
        kwargs.setdefault("input_date_formats", ["%d/%m/%Y", "%Y-%m-%d"])
        kwargs.setdefault("input_time_formats", ["%H:%M"])
        super().__init__(**kwargs)
        self.error_messages["invalid"] = MENSAJE_FECHA
        self.fields[0].error_messages["invalid"] = MENSAJE_FECHA
        self.fields[1].error_messages["invalid"] = "Escribí la hora como HH:MM."
