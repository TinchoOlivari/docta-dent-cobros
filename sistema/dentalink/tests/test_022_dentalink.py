"""022 Fase A: Dentalink caído (FR-006), acciones de envíos (FR-028), fechas (FR-029, FR-038)."""

from unittest.mock import patch

from django.urls import reverse

from payment_instructions.models import User
from sistema.cobros.models import ItemCobro
from sistema.core.tests import SistemaTestCase
from sistema.dentalink.tests import cobro_con_items

TEXTO = "Dentalink no está disponible. Podés cobrar sin conectar con Dentalink."
Estado = ItemCobro.EstadoEnvio


class PantallasAdminTest(SistemaTestCase):
    def setUp(self):
        User.objects.create_user("adm", email="adm@example.com", password="x", role="administrator")
        self.client.login(username="adm", password="x")

    def test_reintentar_con_la_tarea_sin_ejecutar_dice_reintento_en_curso(self):
        cobro = cobro_con_items(["1000.00"])
        item = cobro.items.get()
        ItemCobro.objects.filter(pk=item.pk).update(estado_envio=Estado.ERROR)
        with (
            patch("sistema.dentalink.tasks.enviar_cobro.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            r = self.client.post(
                reverse("dentalink:accion", args=[item.pk, "reintentar"]), follow=True
            )
        delay.assert_called_once_with(cobro.pk)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, Estado.PENDIENTE)
