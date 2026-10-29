from django import forms
from django.utils import timezone

from sistema.core.forms import FechaHoraField
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta

from .models import Terminal


class TerminalForm(forms.ModelForm):
    class Meta:
        model = Terminal
        fields = [
            "nombre", "proveedor", "acepta_credito", "acepta_debito", "acepta_qr",
            "acepta_link", "observaciones",
        ]  # fmt: skip

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields["proveedor"]


def _ahora():
    return timezone.localtime(timezone.now(), ARG).replace(second=0, microsecond=0)


HoraField = FechaHoraField


class AcumulaDesdeForm(forms.Form):
    fecha = HoraField()


def _cuentas(terminal, habilitadas):
    qs = Cuenta.objects.filter(tipo="terminal_pos", proveedor=terminal.proveedor)
    qs = qs.select_related("destinatario").para_selector()
    return qs.habilitadas() if habilitadas else qs


def _selector(field, terminal, habilitadas):
    field.queryset = _cuentas(terminal, habilitadas)
    field.label_from_instance = lambda c: c.etiqueta_selector


class AbrirSesionForm(forms.Form):
    cuenta = forms.ModelChoiceField(queryset=Cuenta.objects.none())
    inicio = HoraField()

    def __init__(self, *args, terminal, **kwargs):
        super().__init__(*args, **kwargs)
        _selector(self.fields["cuenta"], terminal, True)


class CambiarCuentaForm(forms.Form):
    cuenta = forms.ModelChoiceField(queryset=Cuenta.objects.none())
    hora = HoraField(initial=_ahora)

    def __init__(self, *args, terminal, **kwargs):
        super().__init__(*args, **kwargs)
        _selector(self.fields["cuenta"], terminal, True)


class CerrarSesionForm(forms.Form):
    fin = HoraField(initial=_ahora)


class EditarSesionForm(forms.Form):
    cuenta = forms.ModelChoiceField(queryset=Cuenta.objects.none())
    inicio = HoraField()
    fin = HoraField(required=False)

    def __init__(self, *args, terminal, **kwargs):
        super().__init__(*args, **kwargs)
        _selector(self.fields["cuenta"], terminal, False)


class CorregirForm(forms.Form):
    hora = HoraField()
