from celery import shared_task

from . import servicios


@shared_task
def correr_reglas():
    return servicios.correr_reglas()
