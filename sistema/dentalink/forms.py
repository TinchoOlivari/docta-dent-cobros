from django import forms

from .models import Banco, ConfiguracionDentalink


class ConfiguracionForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionDentalink
        fields = [
            "id_prestacion_saldo_a_favor", "nombre_prestacion_saldo_a_favor", "banco_por_defecto",
        ]  # fmt: skip
        labels = {
            "id_prestacion_saldo_a_favor": "Id de la prestación saldo a favor",
            "nombre_prestacion_saldo_a_favor": "Nombre de la prestación saldo a favor",
            "banco_por_defecto": "Banco por defecto",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["banco_por_defecto"].queryset = Banco.objects.filter(vigente=True)
