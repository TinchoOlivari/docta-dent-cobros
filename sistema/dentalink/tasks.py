from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from sistema.cobros.models import ItemCobro

from .servicios import enviar_item, liberar_colgados


@shared_task
def enviar_cobro(cobro_id):
    pks = ItemCobro.objects.filter(
        cobro_id=cobro_id, estado_envio=ItemCobro.EstadoEnvio.PENDIENTE
    ).order_by("numero")
    for pk in list(pks.values_list("pk", flat=True)):
        enviar_item(pk)


@shared_task
def enviar_pendientes():
    liberar_colgados()
    ahora = timezone.now()
    pks = ItemCobro.objects.filter(
        Q(proximo_intento__isnull=True) | Q(proximo_intento__lte=ahora),
        estado_envio=ItemCobro.EstadoEnvio.PENDIENTE,
        en_curso_desde__isnull=True,
        cobro__estado="finalizado",
    ).order_by("pk")
    for pk in list(pks.values_list("pk", flat=True)):
        # Hora de cada envío, no la del arranque: con una fila larga, liberar_colgados de otra
        # corrida vería un envío vivo como colgado, y "Reenviar" pagaría dos veces.
        enviar_item(pk)
