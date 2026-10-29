from django.apps import AppConfig


class AuditoriaConfig(AppConfig):
    name = "sistema.auditoria"
    label = "auditoria"
    verbose_name = "Auditoría"

    def ready(self):
        from . import senales  # noqa: F401
