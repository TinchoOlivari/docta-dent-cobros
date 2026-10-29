from decimal import Decimal
from smtplib import SMTPRecipientsRefused, SMTPServerDisconnected
from unittest.mock import patch

from django.core import mail
from django.db import transaction

from sistema.auditoria.models import Evento
from sistema.avisos import servicios, tasks
from sistema.avisos.models import DireccionAviso
from sistema.avisos.servicios import _enviar, _seguro, umbral
from sistema.avisos.tests import AvisosTestCase

D = Decimal


class UmbralTests(AvisosTestCase):
    def setUp(self):
        super().setUp()

    def test_margen_10_baja_el_tope(self):
        self.assertEqual(umbral(D("500000.00"), 10), D("450000.00"))

    def test_margen_0_es_el_tope(self):
        self.assertEqual(umbral(D("500000.00"), 0), D("500000.00"))

    def test_margen_99_deja_el_uno_por_ciento(self):
        self.assertEqual(umbral(D("500000.00"), 99), D("5000.00"))

    def test_redondea_medio_centavo_hacia_arriba(self):
        self.assertEqual(umbral(D("0.05"), 10), D("0.05"))

    def test_base_cero_es_cero(self):
        self.assertEqual(umbral(D("0.00"), 10), D("0.00"))


class TareaTests(AvisosTestCase):
    def setUp(self):
        super().setUp()

    def test_enviar_aviso_manda_un_email_literal(self):
        tasks.enviar_aviso.delay("a@clinica.test", "Asunto", "Cuerpo")
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual((m.to, m.subject, m.body), (["a@clinica.test"], "Asunto", "Cuerpo"))

    def test_reintenta_y_sale_sin_log_de_error(self):
        falla = SMTPServerDisconnected()
        with patch("django.core.mail.EmailMessage.send", side_effect=[falla, falla, 1]) as send:
            with self.assertNoLogs("sistema.avisos.tasks", "ERROR"):
                tasks.enviar_aviso.delay("a@clinica.test", "A", "C")
        self.assertEqual(send.call_count, 3)

    def test_agota_los_reintentos_loguea_y_no_lanza(self):
        with patch("django.core.mail.EmailMessage.send", side_effect=OSError()) as send:
            with self.assertLogs("sistema.avisos.tasks", "ERROR") as logs:
                tasks.enviar_aviso.delay("a@clinica.test", "A", "C")
        self.assertEqual(send.call_count, tasks.MAX_REINTENTOS + 1)
        self.assertIn("Aviso no enviado", logs.output[0])

    def test_quinto_reintento_exitoso_no_loguea_error(self):
        falla = OSError()
        efectos = [falla] * tasks.MAX_REINTENTOS + [1]
        with patch("django.core.mail.EmailMessage.send", side_effect=efectos):
            with self.assertNoLogs("sistema.avisos.tasks", "ERROR"):
                tasks.enviar_aviso.delay("a@clinica.test", "A", "C")


class EnviarTests(AvisosTestCase):
    def setUp(self):
        super().setUp()
        DireccionAviso.objects.create(email="duenio@clinica.test")
        DireccionAviso.objects.create(email="admin@clinica.test")

    def test_un_email_por_direccion(self):
        with self.captureOnCommitCallbacks(execute=True):
            _enviar("A", ["x", "y"])
        self.assertEqual(
            sorted(m.to[0] for m in mail.outbox),
            [
                "admin@clinica.test",
                "duenio@clinica.test",
            ],
        )
        self.assertEqual(mail.outbox[0].body, "x\ny")

    def test_lista_vacia_no_envia(self):
        DireccionAviso.objects.all().delete()
        with self.captureOnCommitCallbacks(execute=True):
            _enviar("A", ["x"])
        self.assertEqual(len(mail.outbox), 0)

    def test_transaccion_revertida_no_envia(self):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    _enviar("A", ["x"])
                    raise ValueError
            except ValueError:
                pass
        self.assertEqual(len(mail.outbox), 0)

    def test_direccion_rechazada_no_frena_a_la_otra(self):
        real = tasks.EmailMessage.send

        def send(msg, *a, **k):
            if msg.to == ["admin@clinica.test"]:
                raise SMTPRecipientsRefused({})
            return real(msg, *a, **k)

        with patch("django.core.mail.EmailMessage.send", autospec=True, side_effect=send):
            with self.assertLogs("sistema.avisos.tasks", "ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    _enviar("A", ["x"])
        self.assertEqual([m.to for m in mail.outbox], [["duenio@clinica.test"]])

    def test_delay_que_falla_no_lanza_en_on_commit(self):
        # captureOnCommitCallbacks imita el modo robusto de producción y loguea en "django.test".
        with patch.object(tasks.enviar_aviso, "delay", side_effect=ConnectionError()):
            with self.assertLogs("django.test", "ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    _enviar("A", ["x"])

    def test_delay_que_falla_con_ya_loguea_y_no_lanza(self):
        with patch.object(tasks.enviar_aviso, "delay", side_effect=ConnectionError()):
            with self.assertLogs("sistema.avisos.servicios", "ERROR") as logs:
                _enviar("A", ["x"], ya=True)
        self.assertIn("Aviso no encolado", logs.output[0])

    def test_enviar_no_crea_eventos(self):
        with self.captureOnCommitCallbacks(execute=True):
            _enviar("A", ["x"])
        self.assertEqual(Evento.objects.count(), 0)


class SeguroTests(AvisosTestCase):
    def setUp(self):
        super().setUp()

    def test_error_al_armar_no_lanza_ni_deshace_lo_anterior(self):
        @_seguro
        def evento():
            DireccionAviso.objects.create(email="b@clinica.test")
            raise ValueError("boom")

        with transaction.atomic():
            DireccionAviso.objects.create(email="a@clinica.test")
            with self.assertLogs("sistema.avisos.servicios", "ERROR") as logs:
                self.assertIsNone(evento())
        self.assertIn("Aviso no armado: evento", logs.output[0])
        self.assertEqual(
            list(DireccionAviso.objects.values_list("email", flat=True)), ["a@clinica.test"]
        )

    def test_margen_sin_fila_es_10_y_no_crea_la_fila(self):
        self.assertEqual(servicios.margen(), 10)
        from sistema.avisos.models import ConfiguracionAvisos

        self.assertFalse(ConfiguracionAvisos.objects.exists())
