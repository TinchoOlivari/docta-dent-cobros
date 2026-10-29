"""
CI settings.

Igual que `local`, pero contra Postgres. Producción y staging corren Postgres.
Testear sobre SQLite esconde diferencias de motor.

No usamos `staging` para esto: ese módulo tiene `CELERY_TASK_ALWAYS_EAGER = False`
y los tests de tasks necesitan un broker real si no está en `True`.
"""

import os

from .local import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "doctadent_ci"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}
