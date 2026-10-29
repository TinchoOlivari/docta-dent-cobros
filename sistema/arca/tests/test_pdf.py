import json
from base64 import b64decode
from datetime import date
from decimal import Decimal
from unittest import mock

import pymupdf
from django.urls import reverse

from payment_instructions.models import User
from sistema.arca.pdf import URL_QR, nombre_archivo, pdf_comprobante, url_qr
from sistema.arca.tests.fabricas import AHORA, CAE, cobro_facturable, factura_prueba
from sistema.core.tests import SistemaTestCase

D = Decimal
EMITIDA = dict(
    estado="emitido",
    cbte_tipo=6,
    punto_venta=3,
    numero=42,
    fecha=date(2026, 10, 20),
    neto="53719.01",
    iva="11280.99",
    total="65000.00",
    cae=CAE,
    cae_vence=date(2026, 10, 30),
    receptor_nombre="Ana Prueba",
    doc_tipo=96,
    doc_numero="30111222",
)


def decodificar(url):
    assert url.startswith(URL_QR)
    return b64decode(url[len(URL_QR) :]).decode()


class FacturaBase(SistemaTestCase):
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
        self.profesional = User.objects.create_user(
            "pro", email="pro@example.com", password="x", role="professional"
        )

    def emitida(self, **campos):
        c = factura_prueba(cobro_facturable(), **(EMITIDA | campos))
        c.refresh_from_db()  # montos como Decimal
        return c


class QrTest(FacturaBase):
    def test_url_qr_decodifica_a_los_datos_que_pide_arca(self):
        texto = decodificar(url_qr(self.emitida()))
        self.assertEqual(
            json.loads(texto, parse_float=D),
            {
                "ver": 1,
                "fecha": "2026-10-20",
                "cuit": 20000000001,
                "ptoVta": 3,
                "tipoCmp": 6,
                "nroCmp": 42,
                "importe": D("65000.00"),
                "moneda": "PES",
                "ctz": 1,
                "tipoDocRec": 96,
                "nroDocRec": 30111222,
                "tipoCodAut": "E",
                "codAut": int(CAE),
            },
        )
        self.assertNotIn(" ", texto)  # separators=(",", ":")
        self.assertIn('"importe":65000.00,', texto)

    def test_importe_con_2_decimales_en_los_limites(self):
        for numero, (total, esperado) in enumerate(
            [
                ("0.00", "0.00"),
                ("0.01", "0.01"),
                ("0.10", "0.10"),
                ("1000000.00", "1000000.00"),
                ("1234567.89", "1234567.89"),
            ],
            start=1,
        ):
            with self.subTest(total=total):
                texto = decodificar(
                    url_qr(self.emitida(total=total, neto=total, iva="0.00", numero=numero))
                )
                self.assertIn(f'"importe":{esperado},', texto)

    def test_consumidor_final_va_con_documento_cero(self):
        texto = decodificar(url_qr(self.emitida(doc_tipo=99, doc_numero="0")))
        self.assertIn('"tipoDocRec":99,"nroDocRec":0,', texto)

    def test_no_emitido_lanza_value_error(self):
        c = self.emitida()
        for estado in ("pendiente", "error", "descartado"):
            with self.subTest(estado=estado):
                c.estado = estado
                with self.assertRaises(ValueError):
                    url_qr(c)
                with self.assertRaises(ValueError):
                    pdf_comprobante(c)


class PdfTest(FacturaBase):
    def paginas(self, comprobante):
        return pymupdf.open(stream=pdf_comprobante(comprobante), filetype="pdf")

    def test_pdf_tiene_1_pagina_con_numero_total_cae_y_1_imagen(self):
        c = self.emitida()
        self.assertTrue(pdf_comprobante(c).startswith(b"%PDF"))
        doc = self.paginas(c)
        self.assertEqual(len(doc), 1)
        texto = doc[0].get_text()
        for esperado in ("00003-00000042", "65.000,00", CAE, "Factura B", "30/10/2026"):
            self.assertIn(esperado, texto)
        self.assertEqual(len(doc[0].get_images()), 1)

    def test_nota_de_credito_dice_nota_de_credito_y_cambia_el_nombre_del_archivo(self):
        factura = self.emitida()
        nota = factura_prueba(
            factura.cobro,
            **(EMITIDA | dict(tipo="nota_credito", asociado=factura, numero=7, cbte_tipo=8)),
            vigente=False,
        )
        nota.refresh_from_db()
        self.assertIn("Nota de crédito B", self.paginas(nota)[0].get_text())
        self.assertEqual(nombre_archivo(nota), "nota-credito-00003-00000007.pdf")
        self.assertEqual(nombre_archivo(factura), "factura-00003-00000042.pdf")


class PantallasTest(FacturaBase):
    def url_pdf(self, c):
        return reverse("arca:pdf", args=[c.pk])

    def test_administrador_descarga_el_pdf(self):
        c = self.emitida()
        self.client.force_login(self.admin)
        r = self.client.get(self.url_pdf(c))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(
            r["Content-Disposition"], 'attachment; filename="factura-00003-00000042.pdf"'
        )
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_comprobante_que_no_esta_emitido_da_404(self):
        self.client.force_login(self.admin)
        for estado, extra in [
            ("pendiente", {}),
            ("error", {"motivo": "ARCA caído"}),
            ("descartado", {}),
        ]:
            with self.subTest(estado=estado):
                c = factura_prueba(cobro_facturable(), estado=estado, **extra, vigente=False)
                self.assertEqual(self.client.get(self.url_pdf(c)).status_code, 404)

    def test_pdf_inexistente_da_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("arca:pdf", args=[999999])).status_code, 404)

    def test_operador_y_profesional_no_descargan_ni_el_pdf_de_su_propio_cobro(self):
        c = factura_prueba(cobro_facturable(creado_por=self.operador), **EMITIDA)
        for usuario in (self.operador, self.profesional):
            with self.subTest(rol=usuario.role):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(self.url_pdf(c)).status_code, 403)

    def test_sin_sesion_va_al_login(self):
        r = self.client.get(self.url_pdf(self.emitida()))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("usuarios:login"), r["Location"])

    def test_detalle_del_administrador_muestra_numero_cae_y_enlace_al_pdf(self):
        c = self.emitida()
        self.client.force_login(self.admin)
        r = self.client.get(reverse("cobros:detalle", args=[c.cobro_id]))
        self.assertContains(r, "00003-00000042")
        self.assertContains(r, CAE)

    def test_detalle_muestra_el_motivo_de_una_factura_con_error(self):
        c = factura_prueba(cobro_facturable(), estado="error", motivo="Certificado vencido")
        self.client.force_login(self.admin)
        r = self.client.get(reverse("cobros:detalle", args=[c.cobro_id]))
        self.assertContains(r, "Certificado vencido")

    def test_operador_ve_su_cobro_sin_la_seccion_factura(self):
        c = factura_prueba(cobro_facturable(creado_por=self.operador), **EMITIDA)
        self.client.force_login(self.operador)
        r = self.client.get(reverse("cobros:detalle", args=[c.cobro_id]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "00003-00000042")
        self.assertNotContains(r, CAE)
        self.assertNotContains(r, self.url_pdf(c))

    def test_operador_no_ve_el_cobro_facturado_de_otro(self):
        c = self.emitida()
        self.client.force_login(self.operador)
        self.assertEqual(
            self.client.get(reverse("cobros:detalle", args=[c.cobro_id])).status_code, 404
        )

    def test_profesional_no_ve_el_detalle_de_un_cobro_facturado(self):
        c = self.emitida()
        self.client.force_login(self.profesional)
        self.assertEqual(
            self.client.get(reverse("cobros:detalle", args=[c.cobro_id])).status_code, 403
        )
