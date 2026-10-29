from io import BytesIO

import fitz
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from django.test import SimpleTestCase

from sistema.cobros import comprobante

TIPO = "El comprobante debe ser una imagen (JPEG, PNG o GIF) o un PDF."


def imagen(formato="PNG", nombre="c.png"):
    salida = BytesIO()
    Image.new("RGB", (10, 10), "white").save(salida, format=formato)
    return SimpleUploadedFile(nombre, salida.getvalue())


def pdf(paginas=1):
    doc = fitz.open()
    for _ in range(paginas):
        doc.new_page()
    return SimpleUploadedFile("c.pdf", doc.tobytes())


class ComprobanteTest(SimpleTestCase):
    def assertJpeg(self, archivo):
        self.assertEqual(archivo.name, "comprobante.jpg")
        self.assertEqual(Image.open(BytesIO(archivo.read())).format, "JPEG")

    def test_exactamente_5_mb_se_acepta(self):
        archivo = imagen()
        archivo.size = comprobante.MAX_BYTES
        self.assertJpeg(comprobante.validar_y_comprimir(archivo))

    def test_5_mb_mas_un_byte_se_rechaza(self):
        archivo = imagen()
        archivo.size = comprobante.MAX_BYTES + 1
        with self.assertRaisesMessage(ValidationError, "El comprobante pesa más de 5 MB."):
            comprobante.validar_y_comprimir(archivo)

    def test_5_mb_menos_un_byte_se_acepta(self):
        archivo = imagen()
        archivo.size = comprobante.MAX_BYTES - 1
        self.assertJpeg(comprobante.validar_y_comprimir(archivo))

    def test_docx_se_rechaza(self):
        archivo = SimpleUploadedFile("c.docx", b"PK\x03\x04" + b"\x00" * 50)
        with self.assertRaisesMessage(ValidationError, TIPO):
            comprobante.validar_y_comprimir(archivo)

    def test_texto_con_nombre_pdf_se_rechaza(self):
        archivo = SimpleUploadedFile("c.pdf", b"hola", content_type="application/pdf")
        with self.assertRaisesMessage(ValidationError, TIPO):
            comprobante.validar_y_comprimir(archivo)

    def test_png_pasa_a_jpeg(self):
        self.assertJpeg(comprobante.validar_y_comprimir(imagen()))

    def test_gif_se_acepta(self):
        self.assertJpeg(comprobante.validar_y_comprimir(imagen("GIF", "c.gif")))

    def test_pdf_de_una_pagina_pasa_a_jpeg(self):
        self.assertJpeg(comprobante.validar_y_comprimir(pdf()))

    def test_pdf_de_varias_paginas_guarda_la_primera(self):
        self.assertJpeg(comprobante.validar_y_comprimir(pdf(3)))

    def test_archivo_vacio_se_rechaza(self):
        with self.assertRaisesMessage(ValidationError, TIPO):
            comprobante.validar_y_comprimir(SimpleUploadedFile("c.png", b""))
