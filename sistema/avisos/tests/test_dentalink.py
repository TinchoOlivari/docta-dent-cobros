from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail

from sistema.avisos.models import DireccionAviso
from sistema.avisos.tests import AvisosTestCase
from sistema.cobros.models import ItemCobro
from sistema.dentalink import servicios
from sistema.dentalink.cliente import Resultado
from sistema.dentalink.models import MedioPago
from sistema.dentalink.tests import cobro_con_items
from sistema.dentalink.tests.test_envio import AHORA, CREAR, EnvioBase

CAIDO = Resultado("reintentable", error="caído")
DELAY = "sistema.dentalink.tasks.enviar_cobro.delay"


class BaseAvisoDentalink(AvisosTestCase, EnvioBase):
    def setUp(self):
        super().setUp()
        for email in ("duenio@clinica.test", "admin@clinica.test"):
            DireccionAviso.objects.create(email=email)

    def enviar(self, item, resultados, ahora=AHORA):
        with self.captureOnCommitCallbacks(execute=True), patch(CREAR, side_effect=resultados):
            return servicios.enviar_item(item.pk, ahora=ahora)

    def dos_items(self):
        cobro = cobro_con_items(["1000.00", "2000.00"])
        return cobro, cobro.items.get(numero=2)

    def asuntos(self):
        return [m.subject for m in mail.outbox]


class ErrorTests(BaseAvisoDentalink):
    def test_item_en_error_tras_el_ultimo_reintento_avisa(self):
        cobro, item = self.dos_items()
        ahora = AHORA
        for _ in range(servicios.MAX_INTENTOS):
            self.enviar(item, [CAIDO], ahora=ahora)
            ahora += timedelta(hours=1)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "error")
        asunto = f"Envío a Dentalink con error: cobro {cobro.pk}"
        self.assertEqual(self.asuntos(), [asunto] * 2)  # 5 reintentos Pendiente: 0 avisos
        cuerpo = mail.outbox[0].body
        for texto in (
            f"Ítem: {cobro.pk}-2",
            cobro.nombre_paciente,
            "$2.000,00",
            "Error de Dentalink: caído",
            f"/sistema/cobros/{cobro.pk}/#item-2",
        ):
            self.assertIn(texto, cuerpo)

    def test_error_4xx_avisa_y_un_reintento_pendiente_no(self):
        _, item = self.dos_items()
        self.enviar(item, [CAIDO])
        self.assertEqual(mail.outbox, [])
        item.refresh_from_db()
        self.enviar(
            item, [Resultado("rechazado", error="ya pagada")], ahora=AHORA + timedelta(hours=1)
        )
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Error de Dentalink: ya pagada", mail.outbox[0].body)

    def test_enviado_no_avisa(self):
        _, item = self.dos_items()
        self.enviar(item, [Resultado("ok", id_pago=1)])
        self.assertEqual(mail.outbox, [])

    def test_falta_dato_avisa_error(self):
        _, item = self.dos_items()
        MedioPago.objects.update(nombre="Otro")
        self.enviar(item, [])
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "error")
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("no está en la copia", mail.outbox[0].body)

    def test_reintentar_y_volver_a_fallar_manda_aviso_nuevo(self):
        cobro, item = self.dos_items()
        self.enviar(item, [Resultado("rechazado", error="uno")])
        admin = get_user_model().objects.create_user(
            "adm", email="adm@example.com", role="administrator"
        )
        with patch(DELAY):
            servicios.reintentar(ItemCobro.objects.get(pk=item.pk), usuario=admin)
        self.enviar(item, [Resultado("rechazado", error="dos")])
        self.assertEqual(len(mail.outbox), 4)
        self.assertIn("Error de Dentalink: dos", mail.outbox[-1].body)

    def test_el_email_no_trae_cbu_ni_ultimos_4(self):
        cobro, item = self.dos_items()
        self.enviar(item, [Resultado("rechazado", error="x")])
        cuerpo = mail.outbox[0].body
        self.assertNotIn("CBU", cuerpo)
        self.assertNotIn("4 dígitos", cuerpo)


class RevisarTests(BaseAvisoDentalink):
    def test_respuesta_sin_id_avisa_revisar(self):
        cobro, item = self.dos_items()
        self.enviar(item, [Resultado("sin_id", error="x")])
        self.assertEqual(self.asuntos(), [f"Revisar en Dentalink: cobro {cobro.pk}"] * 2)
        self.assertIn(
            "Verificá en Dentalink si el pago se creó antes de reenviarlo.", mail.outbox[0].body
        )
        self.assertNotIn("Error de Dentalink", mail.outbox[0].body)

    def test_liberar_colgados_avisa_revisar(self):
        cobro, item = self.dos_items()
        ItemCobro.objects.filter(pk=item.pk).update(
            en_curso_desde=AHORA - timedelta(minutes=10, seconds=1)
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(servicios.liberar_colgados(ahora=AHORA), 1)
        self.assertEqual(self.asuntos(), [f"Revisar en Dentalink: cobro {cobro.pk}"] * 2)
