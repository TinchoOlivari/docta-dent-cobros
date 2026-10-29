from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from sistema.auditoria.servicios import esta_bloqueado


class BackendConBloqueo(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        user = User._default_manager.filter(**{User.USERNAME_FIELD: username}).first()
        if user is not None and esta_bloqueado(user):
            if request is not None:
                request.login_bloqueado = True
            return None
        return super().authenticate(request, username, password, **kwargs)
