import shutil
import tempfile

from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings


class MediaAisladaMixin:
    # MEDIA_ROOT propio por test: los workers de --parallel no comparten archivos.
    def setUp(self):
        super().setUp()
        media = tempfile.mkdtemp(prefix="test-media-sistema-")
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        cm = override_settings(MEDIA_ROOT=media)
        cm.enable()
        self.addCleanup(cm.disable)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
)
class SistemaTestCase(MediaAisladaMixin, TestCase):
    pass


@override_settings(
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
)
class SistemaTransactionTestCase(MediaAisladaMixin, TransactionTestCase):
    # El flush borra los datos sembrados por migraciones (Proveedor); se restauran al terminar.
    serialized_rollback = True

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        # El último flush deja la base vacía; con --keepdb rompería las corridas siguientes.
        for alias in cls._databases_names(include_mirrors=False):
            contenido = getattr(connections[alias], "_test_serialized_contents", None)
            if contenido:
                connections[alias].creation.deserialize_db_from_string(contenido)
