"""
Django settings for payment_system project.
Base configuration shared across all environments.
"""

import os
from pathlib import Path

from celery.schedules import crontab

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("SECRET_KEY", "fallback-secret-key-change-in-production")

# Maximum upload size in bytes
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB

# Nombre del ambiente. Solo "staging" muestra el aviso visual de no-producción.
ENVIRONMENT = "production"

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django_extensions",
    "django_cotton.apps.SimpleAppConfig",
    "django_filters",
    "django_celery_beat",
    "payment_instructions",
    "adminsortable2",
    "sistema.core",
    "sistema.destinatarios",
    "sistema.usuarios",
    "sistema.auditoria",
    "sistema.dentalink",
    "sistema.terminales",
    "sistema.cobros",
    "sistema.asignacion",
    "sistema.avisos",
    "sistema.automatizacion",
    "sistema.reportes",
    "sistema.arca",
    "sistema.migracion",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "sistema.auditoria.middleware.contexto_auditoria",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "payment_system.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        # Sin APP_DIRS: los loaders van explícitos para que cotton (016) vaya primero.
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django_cotton.cotton_loader.Loader",
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                )
            ],
            "builtins": ["django_cotton.templatetags.cotton"],
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "sistema.core.context_processors.ambiente",
            ],
        },
    },
]

WSGI_APPLICATION = "payment_system.wsgi.application"


# Database
# Cada ambiente (local/ci/staging/production) define su propio DATABASES con
# Postgres. No hay default acá a propósito: sin overridearlo, falta config.


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
LANGUAGE_CODE = "es-ar"
USE_L10N = True
TIME_ZONE = "America/Argentina/Buenos_Aires"
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files (uploads)
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Custom User Model
AUTH_USER_MODEL = "payment_instructions.User"

AUTHENTICATION_BACKENDS = ["sistema.auditoria.backends.BackendConBloqueo"]

# Login/Logout URLs
LOGIN_URL = "usuarios:login"
LOGIN_REDIRECT_URL = "usuarios:inicio"
LOGOUT_REDIRECT_URL = "usuarios:login"

# Celery configuration
# Avisos por email (009). Sin variables, el email no sale: los avisos fallan al log.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "0") == "1"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "avisos@localhost")
EMAIL_TIMEOUT = 10
SISTEMA_URL = os.environ.get("SISTEMA_URL", "http://localhost:8000").rstrip("/")

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TIMEZONE = "America/Argentina/Buenos_Aires"
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {
    "dentalink-enviar-pendientes": {
        "task": "sistema.dentalink.tasks.enviar_pendientes",
        "schedule": 60.0,
    },
    "automatizacion-correr-reglas": {
        "task": "sistema.automatizacion.tasks.correr_reglas",
        "schedule": crontab(),
    },
    "arca-emitir-pendientes": {
        "task": "sistema.arca.tasks.emitir_pendientes",
        "schedule": 60.0,
    },
    "arca-avisar-certificados": {
        "task": "sistema.arca.tasks.avisar_certificados",
        "schedule": crontab(hour=8, minute=0),
    },
}

# ARCA (014). Solo producción usa el ambiente de producción de ARCA (FR-032).
ARCA_PRODUCCION = False

# Dentalink (005). El token viene del entorno.
DENTALINK_URL = os.environ.get("DENTALINK_URL", "https://api.dentalink.healthatom.com/api/v1")
DENTALINK_TOKEN = os.environ.get("DENTALINK_TOKEN", "")
# 024/FR-020: web de Dentalink para abrir un tratamiento.
DENTALINK_WEB = os.environ.get("DENTALINK_WEB", "https://clinica.softwaredentalink.com")

# Ensure log directory exists and configure logging
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "file": {
            "level": "INFO",
            "class": "logging.FileHandler",
            "filename": "logs/general.log",
            "formatter": "standard",
        },
    },
    "loggers": {
        "payment_instructions": {
            "handlers": ["file"],
            "level": "WARNING",
            "propagate": False,
        },
    },
    "formatters": {
        "standard": {
            "format": "%(levelname)s %(asctime)s %(filename)s:%(lineno)d %(name)s %(message)s "
        },
    },
}

# Sentry. Sin SENTRY_DSN en el entorno no hace nada, así que `local` y `ci`
# quedan mudos. Las opciones de privacidad están en payment_system/sentry.py.
from payment_system.sentry import init_sentry

init_sentry()
