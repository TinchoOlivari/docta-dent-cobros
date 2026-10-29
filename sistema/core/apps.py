from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "sistema.core"
    label = "core"

    def ready(self):
        from . import checks  # noqa: F401  (registra los chequeos de `check --deploy`)
