from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile

from sistema.arca import servicios, tasks
from sistema.arca.tests.fabricas import AHORA, certificado_prueba, emisor_prueba
from sistema.avisos.models import DireccionAviso
from sistema.avisos.tests import AvisosTestCase

ENVIAR = "sistema.avisos.servicios._enviar"
LOGGER = "sistema.avisos.servicios"


class AvisoCertificadoTest(AvisosTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        DireccionAviso.objects.create(email="duenio@clinica.test")

    def avisar(self):
        with self.captureOnCommitCallbacks(execute=True):
            servicios.avisar_certificados()

    def emisor(self, dias, **campos):
        return emisor_prueba(certificado_vence=AHORA + timedelta(days=dias), **campos)

    def test_certificado_a_31_dias_no_avisa(self):
        self.emisor(31)
        self.avisar()
        self.assertEqual(len(mail.outbox), 0)

    def test_certificado_a_30_dias_avisa(self):
        self.emisor(30)
        self.avisar()
        self.assertEqual(len(mail.outbox), 1)

    def test_certificado_a_29_dias_avisa(self):
        self.emisor(29)
        self.avisar()
        self.assertEqual(len(mail.outbox), 1)

    def test_certificado_que_vence_hoy_avisa(self):
        self.emisor(0)
        self.avisar()
        self.assertEqual(len(mail.outbox), 1)

    def test_segunda_corrida_no_repite_el_aviso(self):
        emisor = self.emisor(20)
        self.avisar()
        self.avisar()
        self.assertEqual(len(mail.outbox), 1)
        emisor.refresh_from_db()
        self.assertEqual(emisor.aviso_certificado, AHORA)

    def test_emisor_inactivo_a_10_dias_no_avisa(self):
        self.emisor(10, estado="inactivo")
        self.avisar()
        self.assertEqual(len(mail.outbox), 0)

    def test_emisor_sin_certificado_no_avisa(self):
        emisor_prueba(certificado="", certificado_vence=None)
        self.avisar()
        self.assertEqual(len(mail.outbox), 0)

    def test_la_tarea_diaria_avisa(self):
        self.emisor(20)
        with self.captureOnCommitCallbacks(execute=True):
            tasks.avisar_certificados()
        self.assertEqual(len(mail.outbox), 1)

    def test_carga_de_certificado_a_25_dias_avisa_en_el_momento(self):
        emisor = self.emisor(365)
        _, cert = certificado_prueba()
        vence = AHORA + timedelta(days=25)
        with (
            mock.patch("sistema.arca.servicios.leer_certificado", return_value=vence),
            self.captureOnCommitCallbacks(execute=True),
        ):
            servicios.editar_emisor(
                emisor, usuario=None, certificado=SimpleUploadedFile("c.pem", cert)
            )
        self.assertEqual(len(mail.outbox), 1)
        emisor.refresh_from_db()
        self.assertEqual(emisor.aviso_certificado, AHORA)

    def test_certificado_nuevo_despues_de_un_aviso_avisa_otra_vez(self):
        emisor = self.emisor(20)
        self.avisar()
        _, cert = certificado_prueba()
        with (
            mock.patch(
                "sistema.arca.servicios.leer_certificado", return_value=AHORA + timedelta(days=10)
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            servicios.editar_emisor(
                emisor, usuario=None, certificado=SimpleUploadedFile("c.pem", cert)
            )
        self.assertEqual(len(mail.outbox), 2)

    def test_el_aviso_dice_emisor_cuit_fecha_y_enlace_y_no_lleva_pem(self):
        emisor = self.emisor(20)
        self.avisar()
        m = mail.outbox[0]
        self.assertEqual(
            m.subject, "Certificado de ARCA de Clínica Prueba S.A.S. vence el 09/11/2026"
        )
        for texto in (
            "Emisor: Clínica Prueba S.A.S.",
            "CUIT: 20000000001",
            "Vencimiento del certificado: 09/11/2026",
            "Renová el certificado en ARCA y cargalo en el emisor (manual de facturación ARCA).",
            f"/arca/emisores/{emisor.pk}/",
        ):
            self.assertIn(texto, m.body)
        for secreto in ("BEGIN", "PRIVATE"):
            self.assertNotIn(secreto, m.body + m.subject)

    def test_un_error_en_el_aviso_no_deshace_la_edicion(self):
        emisor = self.emisor(365)
        _, cert = certificado_prueba()
        vence = AHORA + timedelta(days=25)
        with (
            mock.patch("sistema.arca.servicios.leer_certificado", return_value=vence),
            mock.patch(ENVIAR, side_effect=RuntimeError("falla")),
            self.assertLogs(LOGGER, "ERROR") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            servicios.editar_emisor(
                emisor,
                usuario=None,
                razon_social="Otra S.A.",
                certificado=SimpleUploadedFile("c.pem", cert),
            )
        emisor.refresh_from_db()
        self.assertEqual(emisor.razon_social, "Otra S.A.")
        self.assertEqual(emisor.certificado_vence, vence)
        self.assertIsNone(emisor.aviso_certificado)  # sin marca: la tarea diaria reintenta
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn("PRIVATE", "\n".join(logs.output))
        self.assertNotIn("BEGIN", "\n".join(logs.output))
        self.avisar()
        self.assertEqual(len(mail.outbox), 1)
