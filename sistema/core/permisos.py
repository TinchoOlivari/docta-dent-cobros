from datetime import timedelta
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from sistema.usuarios import roles

VENTANA_OPERADOR = timedelta(days=15)


def con_rol(*permitidos):
    """Solo mira `User.role` (FR-003): nunca is_superuser, is_staff ni has_perm()."""

    def decorador(view):
        @wraps(view)
        def envuelta(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), reverse("usuarios:login"))
            if request.user.role not in permitidos:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return envuelta

    return decorador


solo_administrador = con_rol(roles.ADMIN)
puede_cobrar = con_rol(roles.ADMIN, roles.OPERATOR)
solo_profesional = con_rol(roles.PROFESSIONAL)


def filtro_cobros_visibles(usuario, ahora=None):
    ahora = ahora or timezone.now()
    if usuario.role == roles.ADMIN:
        return Q()
    if usuario.role == roles.OPERATOR:
        return Q(creado_por=usuario, fecha_alta__gte=ahora - VENTANA_OPERADOR)
    return Q(pk__in=[])
