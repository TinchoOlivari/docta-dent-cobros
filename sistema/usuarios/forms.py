from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, BaseUserCreationForm, SetPasswordForm

from sistema.dentalink.models import Profesional

from . import roles


MENSAJE_CLAVES = "Las dos contraseñas no coinciden."
AYUDA_CLAVE = "Usá al menos 8 caracteres. No uses solo números, tu usuario ni una contraseña común."


class LoginForm(AuthenticationForm):
    def get_invalid_login_error(self):
        # El backend marca el pedido si el usuario está bloqueado (FR-026): con cualquier clave.
        if getattr(self.request, "login_bloqueado", False):
            return forms.ValidationError("Tu cuenta está bloqueada.", code="bloqueada")
        return super().get_invalid_login_error()


class ClaveForm(SetPasswordForm):
    error_messages = {**SetPasswordForm.error_messages, "password_mismatch": MENSAJE_CLAVES}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].help_text = AYUDA_CLAVE
        self.fields["new_password2"].help_text = "Repetí la contraseña para verificarla."


class UsuarioAltaForm(BaseUserCreationForm):
    error_messages = {**BaseUserCreationForm.error_messages, "password_mismatch": MENSAJE_CLAVES}
    role = forms.ChoiceField(
        choices=[("", "Elegí un rol")] + list(roles.CHOICES),
        label="Rol",
        error_messages={"required": "Elegí un rol."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = "Hasta 150 caracteres: letras, números y @ . + - _"
        self.fields["password1"].help_text = AYUDA_CLAVE
        self.fields["password2"].help_text = "Repetí la contraseña para verificarla."

    class Meta:
        model = get_user_model()
        fields = ["username", "first_name", "last_name", "role"]
        labels = {"username": "Usuario", "first_name": "Nombre", "last_name": "Apellido"}


class UsuarioEdicionForm(forms.Form):
    first_name = forms.CharField(label="Nombre", max_length=150, required=False)
    last_name = forms.CharField(label="Apellido", max_length=150, required=False)
    role = forms.ChoiceField(choices=roles.CHOICES, label="Rol")
    observaciones = forms.CharField(label="Observaciones", widget=forms.Textarea, required=False)
    profesional_dentalink = forms.ModelChoiceField(
        Profesional.objects.filter(vigente=True),
        required=False,
        label="Profesional de Dentalink",
    )

    def __init__(self, *args, rol=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Solo un profesional se vincula a Dentalink (021/FR-051).
        if rol != roles.PROFESSIONAL:
            del self.fields["profesional_dentalink"]

    def clean(self):
        datos = super().clean()
        prof = datos.get("profesional_dentalink")
        datos["id_profesional_dentalink"] = prof.id_dentalink if prof else None
        datos["nombre_profesional_dentalink"] = prof.nombre if prof else ""
        return datos
