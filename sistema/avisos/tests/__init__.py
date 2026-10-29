from django.test import override_settings

from sistema.core.tests import SistemaTestCase


# Con EAGER_PROPAGATES el primer `retry` de Celery sube como `Retry` hasta quien llama.
# En producción lo absorbe el worker. Acá se apaga para que `.delay` se comporte igual.
@override_settings(CELERY_TASK_EAGER_PROPAGATES=False)
class AvisosTestCase(SistemaTestCase):
    pass
