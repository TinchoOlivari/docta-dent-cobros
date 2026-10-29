from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from payment_instructions.models import User
from sistema.arca.models import Emisor
from sistema.arca.tests.fabricas import AHORA, certificado_prueba, emisor_prueba
from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase

DATOS = {
    "cuit": "20000000001",
    "razon_social": "Clínica S.A.S.",
    "condicion_iva": "responsable_inscripto",
    "punto_venta": "3",
    "tipo_comprobante": "b",
    "alicuota_iva": "21",
    "concepto": "servicios",
    "descripcion": "Servicios odontológicos",
    "ambiente": "prueba",
}


class PantallasEmisorTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.operador = User.objects.create_user(
            "op", email="op@example.com", password="x", role="operator"
        )
        self.client.force_login(self.admin)

    def archivos(self, certificado=None, clave=None):
        k, c = certificado_prueba()
        return {
            "certificado": SimpleUploadedFile("cert.pem", certificado or c),
            "clave": SimpleUploadedFile("clave.key", clave or k),
        }

    def post_alta(self, **archivos):
        return self.client.post(reverse("arca:nuevo_emisor"), DATOS | archivos)

    def test_alta_por_post_con_los_archivos_de_prueba(self):
        r = self.post_alta(**self.archivos())
        emisor = Emisor.objects.get()
        self.assertRedirects(r, reverse("arca:emisor", args=[emisor.pk]))
        self.assertIsNotNone(emisor.certificado_vence)
        self.assertTrue(Evento.objects.filter(entidad="arca.emisor", tipo="alta").exists())

    def test_la_pagina_del_emisor_dice_certificado_cargado_y_no_muestra_el_pem(self):
        emisor = emisor_prueba()
        r = self.client.get(reverse("arca:emisor", args=[emisor.pk]))
        self.assertNotContains(r, "BEGIN")
        self.assertNotContains(r, "PRIVATE")

    def test_la_pagina_despues_de_una_edicion_fallida_no_muestra_el_pem(self):
        emisor = emisor_prueba()
        r = self.client.post(
            reverse("arca:emisor", args=[emisor.pk]),
            DATOS | {"razon_social": ""} | self.archivos(),
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "BEGIN")
        self.assertNotContains(r, "PRIVATE")

    def test_alta_sin_archivos_da_error(self):
        r = self.post_alta()
        self.assertContains(r, "Subí el certificado y la clave.")
        self.assertFalse(Emisor.objects.exists())

    def test_alta_con_un_solo_archivo_da_error(self):
        r = self.post_alta(certificado=self.archivos()["certificado"])
        self.assertContains(r, "Subí el certificado y la clave.")

    def test_alta_con_clave_invalida_no_filtra_el_pem_ni_al_log(self):
        _, cert = certificado_prueba()
        with self.assertLogs("sistema.arca.views", "WARNING") as logs:
            r = self.post_alta(**self.archivos(clave=cert))
        self.assertContains(r, "El archivo no es una clave privada válida.")
        self.assertNotContains(r, "BEGIN")
        self.assertFalse(Emisor.objects.exists())
        self.assertNotIn("BEGIN", "\n".join(logs.output))

    def test_edicion_sin_archivos_conserva_el_certificado(self):
        emisor = emisor_prueba()
        r = self.client.post(reverse("arca:emisor", args=[emisor.pk]), DATOS | {"punto_venta": "4"})
        self.assertRedirects(r, reverse("arca:emisor", args=[emisor.pk]))
        emisor.refresh_from_db()
        self.assertEqual(emisor.punto_venta, 4)
        self.assertIn("BEGIN CERTIFICATE", emisor.certificado)

    def test_desactivar_y_activar_por_post(self):
        emisor = emisor_prueba()
        url = reverse("arca:estado_emisor", args=[emisor.pk])
        self.client.post(url, {"estado": "inactivo"})
        emisor.refresh_from_db()
        self.assertEqual(emisor.estado, "inactivo")
        r = self.client.post(url, {"estado": "inactivo"}, follow=True)
        self.assertContains(r, "El emisor ya está Inactivo.")
        self.client.post(url, {"estado": "activo"})
        emisor.refresh_from_db()
        self.assertEqual(emisor.estado, "activo")

    def test_estado_por_get_da_405(self):
        emisor = emisor_prueba()
        r = self.client.get(reverse("arca:estado_emisor", args=[emisor.pk]))
        self.assertEqual(r.status_code, 405)

    def test_operador_recibe_403_y_sin_login_redirige(self):
        emisor = emisor_prueba()
        urls = [
            reverse("arca:emisores"),
            reverse("arca:nuevo_emisor"),
            reverse("arca:emisor", args=[emisor.pk]),
            reverse("arca:estado_emisor", args=[emisor.pk]),
        ]
        for url in urls:
            self.client.force_login(self.operador)
            self.assertEqual(self.client.post(url, {}).status_code, 403, url)
            self.assertEqual(self.client.get(url).status_code in (403, 405), True, url)
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_emisor_inexistente_da_404(self):
        self.assertEqual(self.client.get(reverse("arca:emisor", args=[999])).status_code, 404)
