from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def sistema_url_publica(app_configs, **kwargs):
    """`SISTEMA_URL` arma los enlaces de los emails: no puede apuntar a la máquina local (FR-048)."""
    url = getattr(settings, "SISTEMA_URL", "") or ""
    publica = bool(url.strip()) and not any(h in url for h in ("localhost", "127.0.0.1"))
    if settings.DEBUG or publica:
        return []
    return [
        Warning(
            "SISTEMA_URL debe ser la dirección pública del sistema.",
            hint="Definila con la URL pública, por ejemplo https://cobros.example.com.",
            id="sistema.W001",
        )
    ]
