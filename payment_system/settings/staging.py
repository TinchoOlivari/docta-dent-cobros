"""
Staging environment settings.
"""

import os
from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ENVIRONMENT = "staging"

ALLOWED_HOSTS = [
    "159.69.150.168",  # VPS IP (same host as production)
    "127.0.0.1",
    "localhost",
    "staging.example.com",
]

CSRF_TRUSTED_ORIGINS = [
    "https://staging.example.com:8443",
]

# SSL/HTTPS settings for staging (nginx terminates SSL)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True

# PostgreSQL (staging uses Postgres; production stays on SQLite until validated)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "doctadent_staging"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "db"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {
            "connect_timeout": 10,
        },
    }
}

# Celery with Redis in staging (closer to production behaviour)
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False

# Logging level for staging (more verbose)
LOGGING["loggers"]["payment_instructions"]["level"] = "INFO"
