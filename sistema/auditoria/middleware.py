from contextvars import ContextVar

from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address

_contexto = ContextVar("auditoria", default=None)


def _ip_valida(valor):
    try:
        validate_ipv46_address(valor)
    except ValidationError:
        return None
    return valor


def contexto_auditoria(get_response):
    def middleware(request):
        meta = request.META
        remota = _ip_valida(meta.get("REMOTE_ADDR"))
        ip = _ip_valida(meta.get("HTTP_X_REAL_IP")) or remota
        token = _contexto.set((ip, meta.get("HTTP_USER_AGENT", "")[:300]))
        try:
            return get_response(request)
        finally:
            _contexto.reset(token)

    return middleware


def contexto_actual():
    """(ip, dispositivo) del request en curso; (None, "") fuera de uno."""
    return _contexto.get() or (None, "")
