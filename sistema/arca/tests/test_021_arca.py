"""021 Fase F: facturación ARCA (FR-036 a FR-042). Solo dobles de prueba; datos inventados."""

import csv
import io
from datetime import date, datetime
from unittest import mock

import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import ValidationError
from django.urls import reverse

from payment_instructions.models import User
from sistema.arca import servicios as arca
from sistema.arca.forms import cuit_valido
from sistema.arca.models import ComprobanteFiscal, Emisor
from sistema.arca.pdf import pdf_comprobante
from sistema.arca.tests.fabricas import (
    CAE,
    certificado_prueba,
    cobro_facturable,
    emisor_prueba,
    factura_prueba,
)
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Destinatario

HOY = datetime(2026, 10, 28, 10, 0, tzinfo=ARG)


class Base(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=HOY)
        reloj.start()
        self.addCleanup(reloj.stop)
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.client.force_login(self.admin)


class CuitTest(SistemaTestCase):
    def test_validos_e_invalidos(self):
        # Resto 0 (digito 0): 20301112220. Resto 1 (ningun digito sirve): 2000000001x.
        for cuit in ("20000000001", "30712345671", "20301112220"):
            self.assertEqual(cuit_valido(cuit), cuit)
        malos = ("20000000000", "20301112223", "30712345674", "2000000001", "200000000011")
        for cuit in (*malos, "2000000001" + "0", "2000000001" + "9", "2000000000a"):
            with self.assertRaisesMessage(
                ValidationError, "El CUIT no es válido: revisá el último dígito."
            ):
                cuit_valido(cuit)

    def test_con_guiones_se_guarda_sin_guiones(self):
        self.assertEqual(cuit_valido("30-71234567-1"), "30712345671")


class EmisorPantallasTest(Base):
    def test_cuit_invalido_no_crea_emisor(self):
        k, c = certificado_prueba()
        r = self.client.post(
            reverse("arca:nuevo_emisor"),
            {
                "cuit": "20000000000", "razon_social": "Ejemplo", "condicion_iva": "monotributo",
                "punto_venta": "1", "tipo_comprobante": "c", "alicuota_iva": "0",
                "concepto": "servicios", "descripcion": "x", "ambiente": "prueba",
                "certificado": SimpleUploadedFile("c.pem", c),
                "clave": SimpleUploadedFile("k.pem", k),
            },
        )  # fmt: skip
        self.assertContains(r, "El CUIT no es válido: revisá el último dígito.")
        self.assertEqual(Emisor.objects.count(), 0)

    def test_estados_del_certificado(self):
        casos = [
            (datetime(2026, 10, 27, 23, 59, tzinfo=ARG), "vencido"),
            (datetime(2026, 10, 28, 23, 59, tzinfo=ARG), "hoy"),
            (datetime(2026, 11, 17, 12, 0, tzinfo=ARG), "por_vencer"),
            (datetime(2027, 1, 1, 12, 0, tzinfo=ARG), None),
            (None, None),
        ]
        for i, (vence, esperado) in enumerate(casos):
            with self.subTest(vence=vence):
                e = emisor_prueba(cuit=f"2000000010{i}", certificado_vence=vence)
                self.assertEqual(arca.estado_certificado(e, HOY), esperado)

    def test_subir_un_certificado_vencido_avisa(self):
        e = emisor_prueba()
        k, c = certificado_prueba()
        vencido = datetime(2025, 9, 30, tzinfo=ARG)
        with mock.patch("sistema.arca.servicios.leer_certificado", return_value=vencido):
            r = self.client.post(
                reverse("arca:emisor", args=[e.pk]),
                {
                    "cuit": e.cuit, "razon_social": e.razon_social,
                    "condicion_iva": e.condicion_iva, "punto_venta": "3",
                    "tipo_comprobante": "b", "alicuota_iva": "21", "concepto": "servicios",
                    "descripcion": "x", "ambiente": "prueba",
                    "certificado": SimpleUploadedFile("c.pem", c),
                },
                follow=True,
            )  # fmt: skip
        self.assertContains(
            r, "El certificado ya venció. Las facturas de este emisor van a fallar."
        )

    def test_desactivar_nombra_los_destinatarios_que_lo_usan(self):
        e = emisor_prueba()
        self.assertEqual(arca.texto_desactivar(e), "Ningún destinatario lo usa.")
        for n in ("Ejemplo B", "Ejemplo A", "Ejemplo C"):
            Destinatario.objects.create(
                nombre=n, tipo="clinica", emisor=e, facturacion_activa=n != "Ejemplo C"
            )
        self.assertEqual(
            arca.texto_desactivar(e),
            "Lo usan 2 destinatarios: Ejemplo A, Ejemplo B. "
            "Sus cobros nuevos no se van a facturar.",
        )


class FacturaBloqueTest(Base):
    def detalle(self, factura):
        return self.client.get(reverse("cobros:detalle", args=[factura.cobro_id]))

    def test_factura_b_21_muestra_neto_iva_total_emisor_y_receptor_con_error(self):
        cobro = cobro_facturable(monto="22000.50", dni_paciente="30111222")
        f = factura_prueba(cobro, estado="error", motivo="ARCA caída")
        r = self.detalle(f)
        self.assertContains(r, "Neto: $18.182,23")
        self.assertContains(r, "IVA: $3.818,27")
        self.assertContains(r, "Total: $22.000,50")
        self.assertContains(r, "Receptor: DNI 30111222")


class NotaCreditoObligatoriaTest(Base):
    def emitida(self):
        return factura_prueba(
            cobro_facturable(),
            estado="emitido",
            cbte_tipo=6,
            punto_venta=3,
            numero=1,
            fecha=date(2026, 10, 20),
            neto="53719.01",
            iva="11280.99",
            total="65000.00",
            cae=CAE,
            cae_vence=date(2026, 10, 30),
        )

    def datos(self, **extra):
        return {
            "concepto": "servicios", "descripcion": "Otro texto", "receptor_nombre": "",
            "doc_tipo": "", "doc_numero": "",
        } | extra  # fmt: skip

    def test_sin_respuesta_pide_si_o_no_y_no_emite(self):
        f = self.emitida()
        url = reverse("arca:datos_factura", args=[f.cobro_id])
        r = self.client.post(url, self.datos())
        self.assertContains(r, "Elegí Sí o No.")
        self.assertContains(r, "required")
        self.assertEqual(ComprobanteFiscal.objects.filter(tipo="nota_credito").count(), 0)

    def test_con_respuesta_no_guarda(self):
        f = self.emitida()
        url = reverse("arca:datos_factura", args=[f.cobro_id])
        r = self.client.post(url, self.datos(nota_credito="no"))
        self.assertRedirects(r, reverse("cobros:detalle", args=[f.cobro_id]))

    def test_cuit_invalido_del_receptor(self):
        f = self.emitida()
        url = reverse("arca:datos_factura", args=[f.cobro_id])
        r = self.client.post(
            url, self.datos(doc_tipo="80", doc_numero="20000000000", nota_credito="no")
        )
        self.assertContains(r, "El CUIT no es válido: revisá el último dígito.")

    def test_cuit_con_guiones_se_guarda_sin_guiones(self):
        f = factura_prueba(cobro_facturable())
        url = reverse("arca:datos_factura", args=[f.cobro_id])
        r = self.client.post(url, self.datos(doc_tipo="80", doc_numero="30-71234567-1"))
        self.assertEqual(r.status_code, 302, r.context and r.context["form"].errors)
        f.refresh_from_db()
        self.assertEqual(f.doc_numero, "30712345671")


class PdfTest(Base):
    def texto(self, comprobante):
        doc = pymupdf.open(stream=pdf_comprobante(comprobante), filetype="pdf")
        return " ".join(p.get_text() for p in doc).replace("\n", " ")

    def emitida(self, **campos):
        datos = dict(
            estado="emitido", cbte_tipo=6, punto_venta=3, numero=1, fecha=date(2026, 10, 20),
            neto="53719.01", iva="11280.99", total="65000.00", cae=CAE,
            cae_vence=date(2026, 10, 30), doc_tipo=96, doc_numero="30111222",
            servicio_desde=date(2026, 10, 1), servicio_hasta=date(2026, 10, 31),
        )  # fmt: skip
        f = factura_prueba(cobro_facturable(), **(datos | campos))
        f.refresh_from_db()  # los importes vuelven como Decimal
        return f

    def test_concepto_periodo_y_marca_de_prueba(self):
        t = self.texto(self.emitida())
        self.assertIn("Concepto: Servicios", t)
        self.assertIn("Período del servicio: 01/10/2026 a 31/10/2026", t)
        self.assertIn("SIN VALIDEZ FISCAL", t)
        self.assertIn("AMBIENTE DE PRUEBA", t)

    def test_produccion_no_lleva_marca(self):
        e = emisor_prueba(ambiente="produccion")
        self.assertNotIn("SIN VALIDEZ FISCAL", self.texto(self.emitida(emisor=e)))

    def test_sin_periodo_no_dice_periodo(self):
        t = self.texto(self.emitida(servicio_desde=None, servicio_hasta=None))
        self.assertNotIn("Período del servicio", t)

    def test_nota_de_credito_dice_que_factura_anula(self):
        f = self.emitida()
        nota = factura_prueba(
            cobro_facturable(), tipo="nota_credito", asociado=f, vigente=False,
            estado="emitido", cbte_tipo=8, punto_venta=3, numero=1, fecha=date(2026, 10, 21),
            neto="53719.01", iva="11280.99", total="65000.00", cae=CAE,
            cae_vence=date(2026, 10, 30), doc_tipo=96, doc_numero="30111222",
        )  # fmt: skip
        nota.refresh_from_db()
        self.assertIn("Anula la factura 00003-00000001", self.texto(nota))


class LibroTest(Base):
    def setUp(self):
        super().setUp()
        self.e = emisor_prueba(cuit="30712345671", razon_social="Ejemplo SA")
        factura_prueba(
            cobro_facturable(), emisor=self.e, estado="emitido", cbte_tipo=6, punto_venta=3,
            numero=1, fecha=date(2026, 10, 2), neto="100.00", iva="21.00", total="121.00",
            cae=CAE, cae_vence=date(2026, 10, 12), doc_tipo=96, doc_numero="30111222",
        )  # fmt: skip

    def test_emisor_invalido_da_error_y_no_muestra_otro(self):
        r = self.client.get(reverse("arca:libro"), {"emisor": "999", "mes": "2026-10"})
        self.assertContains(r, "Elegí un emisor de la lista.")
        self.assertNotContains(r, "30111222")

    def test_descarga_lleva_emisor_y_mes_en_el_nombre(self):
        r = self.client.get(
            reverse("arca:libro_descargar"),
            {"emisor": self.e.pk, "mes": "2026-10", "formato": "csv"},
        )
        self.assertIn("libro-iva-30712345671-2026-10.csv", r["Content-Disposition"])
        filas = list(csv.reader(io.StringIO(b"".join(r.streaming_content).decode()), delimiter=";"))
        self.assertEqual(len(filas), 3)  # títulos, 1 comprobante, totales

    def test_descarga_con_emisor_invalido_es_404(self):
        r = self.client.get(reverse("arca:libro_descargar"), {"emisor": "999", "formato": "csv"})
        self.assertEqual(r.status_code, 404)
