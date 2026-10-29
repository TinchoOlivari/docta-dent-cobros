import logging
import smtplib

from celery import shared_task
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)

MAX_REINTENTOS = 5


# max_retries explícito: el default de Celery (3) cortaría antes de los 5 reintentos.
@shared_task(bind=True, max_retries=MAX_REINTENTOS)
def enviar_aviso(self, direccion, asunto, cuerpo):
    try:
        EmailMessage(asunto, cuerpo, to=[direccion]).send()
    except (smtplib.SMTPException, OSError) as exc:
        if self.request.retries >= MAX_REINTENTOS:
            logger.error(
                "Aviso no enviado: direccion=%s asunto=%s", direccion, asunto, exc_info=True
            )
            return
        raise self.retry(exc=exc, countdown=30 * 2**self.request.retries)
