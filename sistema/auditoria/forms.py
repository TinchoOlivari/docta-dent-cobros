from django import forms

from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import ENTIDADES
from sistema.core.forms import FechaField
from sistema.usuarios.servicios import usuarios_visibles

GRUPOS = {
    "Cobros": [
        "cobros.cobro",
        "cobros.itemcobro",
        "cobros.comprobantecobro",
        "cobros.pagocomplementario",
    ],
    "Destinatarios y cuentas": [
        "destinatarios.destinatario",
        "destinatarios.cuenta",
        "destinatarios.proveedor",
    ],
    "Terminales POS": ["terminales.terminal", "terminales.sesion"],
    "Facturación": ["arca.emisor", "arca.comprobantefiscal"],
    "Usuarios": ["payment_instructions.user"],
    "Configuración": [
        "dentalink.configuraciondentalink",
        "dentalink.banco",
        "dentalink.profesional",
        "dentalink.mediopago",
        "avisos.configuracionavisos",
        "avisos.direccionaviso",
        "automatizacion.regla",
    ],
}


class FiltroEventosForm(forms.Form):
    usuario = forms.ChoiceField(
        required=False, error_messages={"invalid_choice": "Elegí un usuario de la lista."}
    )
    entidad = forms.ChoiceField(
        required=False, error_messages={"invalid_choice": "Elegí una entidad de la lista."}
    )
    tipo = forms.ChoiceField(
        required=False, error_messages={"invalid_choice": "Elegí un tipo de la lista."}
    )
    desde = FechaField(required=False)
    hasta = FechaField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        usuarios = usuarios_visibles().order_by("username")
        self.fields["usuario"].choices = [("", "Todos"), ("sistema", "Sistema")] + [
            (str(u.pk), u.username) for u in usuarios
        ]
        self.fields["entidad"].choices = [("", "Todas")] + [
            (grupo, [(k, ENTIDADES[k]) for k in claves]) for grupo, claves in GRUPOS.items()
        ]
        self.fields["tipo"].choices = [("", "Todos")] + list(Evento.Tipo.choices)

    def clean(self):
        datos = super().clean()
        desde, hasta = datos.get("desde"), datos.get("hasta")
        if desde and hasta and desde > hasta:
            raise forms.ValidationError("La fecha desde no puede ser posterior a la fecha hasta.")
        return datos
