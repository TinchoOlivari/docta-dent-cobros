# Ensures the directory is treated as a package for Django test discovery

from django.test import TestCase, override_settings


@override_settings(
    MEDIA_ROOT="/tmp/test-media",
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
)
class BaseTestCase(TestCase):
    """Base test case with global test settings"""

    pass
