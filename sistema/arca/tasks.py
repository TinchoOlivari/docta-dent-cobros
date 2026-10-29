import logging

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from . import servicios
from .models import ComprobanteFiscal

logger = logging.getLogger(__name__)


@shared_task
def emitir(comprobante_id):
    return servicios.emitir(comprobante_id)


@shared_task
def emitir_pendientes():
    """Beat cada 60 s: emite los Pendientes con el próximo intento vencido o sin fecha (FR-024)."""
    ahora = timezone.now()
    try:
        servicios.liberar_colgados(ahora=ahora)
    except Exception:
        logger.exception("No se pudieron liberar los comprobantes colgados")
    ids = (
        ComprobanteFiscal.objects.filter(
            Q(proximo_intento__isnull=True) | Q(proximo_intento__lte=ahora),
            estado=ComprobanteFiscal.Estado.PENDIENTE,
            en_curso_desde__isnull=True,
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    for pk in list(ids):
        try:
            servicios.emitir(pk)
        except Exception:
            logger.exception("Emisión fallida: comprobante=%s", pk)


@shared_task
def avisar_certificados():
    servicios.avisar_certificados()
