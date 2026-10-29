"""
Local development settings.

Postgres, igual que staging y producción. SQLite no se usa en ningún ambiente.
Variables DB_* las pone docker-compose.local.yml; corriendo fuera de Docker,
exportalas vos (default: localhost:5432).
"""

import importlib.util
import os
import sys

from .base import *

# Debug on for local development
DEBUG = True
ENVIRONMENT = "local"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "doctadent_local"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Allow local hosts
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

# CSRF settings for local dev server
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    # el navegador puede forzar https en localhost (HSTS cacheado)
    "https://localhost:8000",
    "https://127.0.0.1:8000",
    "https://localhost",
]

# Disable HTTPS enforcement locally
SECURE_SSL_REDIRECT = False
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False

# Optional: ensure no proxy SSL header interferes locally
SECURE_PROXY_SSL_HEADER = None

# Less noisy logging during development
LOGGING["loggers"]["payment_instructions"]["level"] = "INFO"

# Herramientas de desarrollo (016). debug-toolbar 8 no corre dentro de los tests.
# La imagen de Docker no instala requirements-dev.txt: ahí no se cargan.
if DEBUG and "test" not in sys.argv and importlib.util.find_spec("debug_toolbar"):
    INSTALLED_APPS += ["debug_toolbar", "django_browser_reload"]
    MIDDLEWARE += [
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        "django_browser_reload.middleware.BrowserReloadMiddleware",
    ]
    INTERNAL_IPS = ["127.0.0.1"]
