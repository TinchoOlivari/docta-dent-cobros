"""022 Fase F: facturación ARCA (FR-014, FR-015, FR-016, FR-029, FR-034). Solo dobles; datos inventados."""

import csv
import io
from datetime import date
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from sistema.arca import servicios
from sistema.arca.models import Emisor
from sistema.arca.tests.fabricas import certificado_prueba, emisor_prueba
from payment_instructions.models import User
from sistema.arca.tests import test_datos_factura, test_libro, test_pantallas_emisor
from sistema.core.tests import SistemaTestCase
from sistema.arca.tests.fabricas import AHORA
from sistema.auditoria.models import Evento

DATOS = test_pantallas_emisor.DATOS

# CUIT inventados con dígito verificador válido, uno por caso.
CUITS = {
    "0": "20000000028",
    "-1": "20000000036",
    "100000": "20000000044",
    "1": "20000000001",
    "99999": "20000000052",
}
MSG_PV = "El punto de venta debe ir de 1 a 99999."


class LibroNumeroTest(test_libro.LibroTestBase):
    def test_numero_con_ceros_en_el_limite_chico_y_grande(self):
        self.emitido(date(2026, 10, 1), 1)
        self.emitido(date(2026, 10, 2), 99999999, punto_venta=12345)
        r = self.client.get(reverse("arca:libro"), {"mes": "2026-10"})
        self.assertContains(r, "00003-00000001")
        self.assertContains(r, "12345-99999999")
        self.assertNotContains(r, "N° ")

    def test_fecha_del_csv_es_dia_mes_anio(self):
        self.emitido(date(2026, 10, 25), 7)
        r = self.client.get(
            reverse("arca:libro_descargar"),
            {"emisor": self.emisor.pk, "mes": "2026-10", "formato": "csv"},
        )
        filas = list(
            csv.reader(
                io.StringIO(b"".join(r.streaming_content).decode("utf-8-sig")), delimiter=";"
            )
        )
        self.assertEqual(filas[1][0], "25/10/2026")


class EmisorTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.client.force_login(self.admin)

    def archivos(self):
        k, c = certificado_prueba()
        return {
            "certificado": SimpleUploadedFile("cert.pem", c),
            "clave": SimpleUploadedFile("clave.key", k),
        }

    def post_alta(self, **datos):
        return self.client.post(reverse("arca:nuevo_emisor"), DATOS | datos | self.archivos())

    def test_punto_de_venta_en_los_limites(self):
        for pv, valido in (
            ("0", False),
            ("-1", False),
            ("100000", False),
            ("1", True),
            ("99999", True),
        ):
            with self.subTest(pv=pv):
                r = self.post_alta(punto_venta=pv, cuit=CUITS[pv])
                if valido:
                    self.assertEqual(r.status_code, 302)
                else:
                    self.assertContains(r, MSG_PV)

    def test_alta_dice_emisor_creado_y_edicion_emisor_guardado(self):
        r = self.post_alta()
        self.assertIn("Emisor creado.", [str(m) for m in r.wsgi_request._messages])
        emisor = Emisor.objects.get()
        r = self.client.post(
            reverse("arca:emisor", args=[emisor.pk]), DATOS | {"descripcion": "Otro"}
        )
        self.assertIn("Emisor guardado.", [str(m) for m in r.wsgi_request._messages])

    def test_textos_del_historial_de_archivos(self):
        k, c = certificado_prueba()
        nuevo = mock.patch("sistema.arca.servicios.leer_certificado")
        casos = [
            ({"certificado": SimpleUploadedFile("c.pem", c)}, "Certificado reemplazado."),
            ({"clave": SimpleUploadedFile("k.pem", k)}, "Clave reemplazada."),
            (
                {
                    "certificado": SimpleUploadedFile("c.pem", c),
                    "clave": SimpleUploadedFile("k.pem", k),
                },
                "Certificado y clave reemplazados.",
            ),
        ]
        for i, (archivos, texto) in enumerate(casos):
            with self.subTest(texto=texto), nuevo as leer:
                leer.return_value = AHORA
                emisor = emisor_prueba(cuit=f"2000000010{i}")
                servicios.editar_emisor(emisor, usuario=None, **archivos)
                self.assertEqual(
                    Evento.objects.get(
                        entidad="arca.emisor", entidad_id=emisor.pk, tipo="cambio"
                    ).detalle,
                    texto,
                )


class FacturaBloqueTest(test_datos_factura.DatosBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_sin_cambios_dice_no_hay_cambios_y_sin_comentario_de_fases(self):
        cobro, factura = self.facturado(emitir=False)
        r = self.client.post(
            reverse("arca:datos_factura", args=[cobro.pk]),
            {"concepto": factura.concepto, "descripcion": factura.descripcion},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual([str(m) for m in r.wsgi_request._messages], ["No hay cambios."])

    def test_edicion_real_dice_datos_guardados(self):
        cobro, factura = self.facturado(emitir=False)
        r = self.client.post(
            reverse("arca:datos_factura", args=[cobro.pk]),
            {"concepto": "productos", "descripcion": factura.descripcion},
        )
        self.assertEqual(
            [str(m) for m in r.wsgi_request._messages], ["Datos de la factura guardados."]
        )

    def test_emitida_con_nota_de_credito_dice_guardados_aunque_no_cambia_nada(self):
        # Comportamiento actual: la vista compara con la factura emitida y, si el dato difiere,
        # dice "guardados" aunque la respuesta "no" deje todo igual.
        cobro, factura = self.facturado()
        r = self.client.post(
            reverse("arca:datos_factura", args=[cobro.pk]),
            {"concepto": "productos", "descripcion": factura.descripcion, "nota_credito": "no"},
        )
        self.assertEqual(
            [str(m) for m in r.wsgi_request._messages], ["Datos de la factura guardados."]
        )
        factura.refresh_from_db()
        self.assertEqual(factura.concepto, "servicios")
