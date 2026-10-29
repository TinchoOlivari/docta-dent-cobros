from django import forms

from sistema.core.forms import FechaField

from .models import DOCUMENTOS, ComprobanteFiscal, Emisor


MENSAJE_CUIT = "El CUIT no es válido: revisá el último dígito."
MENSAJE_PV = "El punto de venta debe ir de 1 a 99999."
_PESOS_CUIT = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def cuit_valido(valor) -> str:
    """CUIT/CUIL con dígito verificador (módulo 11), FR-036. Acepta guiones; devuelve 11 dígitos."""
    cuit = str(valor or "").replace("-", "").strip()
    if len(cuit) != 11 or not (cuit.isascii() and cuit.isdigit()):
        raise forms.ValidationError(MENSAJE_CUIT)
    resto = sum(int(d) * p for d, p in zip(cuit, _PESOS_CUIT)) % 11
    if resto == 1 or (0 if resto == 0 else 11 - resto) != int(cuit[10]):
        raise forms.ValidationError(MENSAJE_CUIT)
    return cuit


class EmisorForm(forms.ModelForm):
    # No son campos del modelo: el form nunca carga el PEM guardado (research R3).
    certificado = forms.FileField(required=False, label="Certificado (.crt o .pem)")
    clave = forms.FileField(required=False, label="Clave privada (.key o .pem)")

    punto_venta = forms.IntegerField(
        label="Punto de venta",
        min_value=1,
        max_value=99999,
        error_messages={k: MENSAJE_PV for k in ("invalid", "min_value", "max_value")},
        widget=forms.NumberInput(attrs={"min": 1, "max": 99999}),
    )

    class Meta:
        model = Emisor
        fields = [
            "cuit", "razon_social", "condicion_iva", "punto_venta", "tipo_comprobante",
            "alicuota_iva", "concepto", "descripcion", "ambiente", "observaciones",
        ]  # fmt: skip
        labels = {
            "cuit": "CUIT",
            "razon_social": "Razón social",
            "condicion_iva": "Condición frente al IVA",
            "punto_venta": "Punto de venta",
            "tipo_comprobante": "Tipo de comprobante",
            "alicuota_iva": "Alícuota de IVA",
            "descripcion": "Descripción",
        }

    def clean_cuit(self):
        return cuit_valido(self.cleaned_data["cuit"])

    def clean(self):
        datos = super().clean()
        if not self.instance.pk:
            for campo in ("certificado", "clave"):
                if not datos.get(campo):
                    raise forms.ValidationError("Subí el certificado y la clave.")
        return datos


MENSAJE_SI_NO = "Elegí Sí o No."


class DatosFacturaForm(forms.Form):
    """Datos de la factura vigente del cobro (FR-020). Las reglas las valida el servicio."""

    concepto = forms.ChoiceField(choices=Emisor.Concepto.choices)
    descripcion = forms.CharField(max_length=200, label="Descripción")
    receptor_nombre = forms.CharField(max_length=200, required=False, label="Nombre del receptor")
    doc_tipo = forms.TypedChoiceField(
        choices=[("", "Según el DNI del cobro")] + [(k, v) for k, v in DOCUMENTOS.items()],
        coerce=int,
        empty_value=None,
        required=False,
        label="Tipo de documento",
    )
    doc_numero = forms.CharField(max_length=13, required=False, label="Número de documento")
    servicio_desde = FechaField(required=False, label="Servicio desde")
    servicio_hasta = FechaField(required=False, label="Servicio hasta")
    nota_credito = forms.ChoiceField(
        choices=[("si", "Sí"), ("no", "No")],
        widget=forms.RadioSelect,
        required=False,
        error_messages={"invalid_choice": MENSAJE_SI_NO},
    )

    def __init__(self, *args, factura: ComprobanteFiscal, **kwargs):
        kwargs.setdefault(
            "initial",
            {
                campo: getattr(factura, campo)
                for campo in (
                    "concepto",
                    "descripcion",
                    "receptor_nombre",
                    "doc_tipo",
                    "doc_numero",
                    "servicio_desde",
                    "servicio_hasta",
                )  # fmt: skip
            },
        )
        super().__init__(*args, **kwargs)
        dni = factura.cobro.dni_paciente
        etiqueta = f"Según el DNI del cobro{f' ({dni})' if dni else ''}"
        self.fields["doc_tipo"].choices = [("", etiqueta), *list(DOCUMENTOS.items())]
        if factura.estado == ComprobanteFiscal.Estado.EMITIDO:
            self.fields["nota_credito"].required = True  # FR-040
            self.fields["nota_credito"].error_messages["required"] = MENSAJE_SI_NO

    def clean(self):
        datos = super().clean()
        numero = datos.get("doc_numero")
        if numero:
            if datos.get("doc_tipo") == 80:  # CUIT (FR-036)
                try:
                    datos["doc_numero"] = cuit_valido(numero)
                except forms.ValidationError as e:
                    self.add_error("doc_numero", e)
        return datos

    def datos(self):
        """Los datos para `editar_datos_factura`, sin la respuesta sobre la nota de crédito."""
        return {k: v for k, v in self.cleaned_data.items() if k != "nota_credito"}

    def nota_credito_elegida(self):
        return {"si": True, "no": False}.get(self.cleaned_data.get("nota_credito"))
