from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from sistema.arca.models import Emisor
from sistema.core.forms import MontoDecimalField
from sistema.dentalink.models import Banco

from .models import Cuenta, Destinatario

_MONTOS = ("tope_mensual", "tope_semanal", "minimo_por_pago")
MENSAJE_MONTO = "Escribí un monto válido, por ejemplo 1.500,50."


class DestinatarioForm(forms.ModelForm):
    class Meta:
        model = Destinatario
        fields = [
            "nombre", "cuit", "tipo", "email", "telefono", "usuario_profesional",
            "facturacion_activa", "emisor", "observaciones",
        ]  # fmt: skip
        labels = {
            "cuit": "CUIT o CUIL",
            "telefono": "Teléfono",
            "usuario_profesional": "Usuario del profesional",
            "facturacion_activa": "Facturación activa",
        }
        error_messages = {"emisor": {"invalid_choice": "Elegí un emisor Activo."}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        q = Q(role="professional", is_active=True)
        if self.instance.usuario_profesional_id:
            q |= Q(pk=self.instance.usuario_profesional_id)
        self.fields["usuario_profesional"].queryset = get_user_model().objects.filter(q)
        # Emisores Activos más el actual: un emisor Inactivo no se elige de nuevo (FR-006).
        activos = Q(estado="activo") | Q(pk=self.instance.emisor_id)
        self.fields["emisor"].queryset = Emisor.objects.filter(activos).order_by("razon_social")


class CuentaTransferenciaForm(forms.ModelForm):
    class Meta:
        model = Cuenta
        fields = [
            "destinatario", "titular", "titular_cuit", "banco", "alias", "cbu",
            *_MONTOS, "estado", "observaciones",
        ]  # fmt: skip
        localized_fields = _MONTOS
        labels = {
            "titular_cuit": "CUIT o CUIL del titular",
            "cbu": "CBU o CVU",
            "minimo_por_pago": "Mínimo por pago",
        }
        error_messages = {m: {"invalid": MENSAJE_MONTO} for m in _MONTOS}
        field_classes = dict.fromkeys(_MONTOS, MontoDecimalField)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["destinatario"].queryset = Destinatario.objects.visibles()
        nombres = list(Banco.objects.filter(vigente=True).values_list("nombre", flat=True))
        if nombres:
            actual = self.instance.banco
            if actual and actual not in nombres:
                nombres.append(actual)
            self.fields["banco"] = forms.ChoiceField(
                required=False,
                label=self.fields["banco"].label,
                choices=[("", "—")] + [(n, n) for n in nombres],
            )


class CuentaTransferenciaEdicionForm(CuentaTransferenciaForm):
    class Meta(CuentaTransferenciaForm.Meta):
        fields = [f for f in CuentaTransferenciaForm.Meta.fields if f != "estado"]


class _CuentaPosBase(forms.ModelForm):
    class Meta:
        model = Cuenta
        fields = ["destinatario", "titular", "objetivo", "observaciones"]
        localized_fields = ["objetivo"]
        error_messages = {"objetivo": {"invalid": MENSAJE_MONTO}}
        field_classes = {"objetivo": MontoDecimalField}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["destinatario"].queryset = Destinatario.objects.visibles()


class CuentaPosEdicionForm(_CuentaPosBase):
    pass


class CuentaPosForm(_CuentaPosBase):
    class Meta(_CuentaPosBase.Meta):
        fields = ["destinatario", "titular", "proveedor", "objetivo", "estado", "observaciones"]


class ProveedorForm(forms.Form):
    nombre = forms.CharField(max_length=60)


class CuentaEfectivoForm(forms.ModelForm):
    class Meta:
        model = Cuenta
        fields = ["destinatario", "titular", "estado", "observaciones"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["destinatario"].queryset = Destinatario.objects.visibles()


class CuentaEfectivoEdicionForm(CuentaEfectivoForm):
    class Meta(CuentaEfectivoForm.Meta):
        fields = ["destinatario", "titular", "observaciones"]
