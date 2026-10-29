"""022 C: zip del filtro, descarga por ítem, reporte del profesional."""

import zipfile
from decimal import Decimal
from unittest import mock

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import load_workbook
from io import BytesIO

from sistema.cobros.models import Cobro, ItemCobro
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta
from sistema.reportes import servicios

from . import DatosMartinO

D = Decimal


class ZipDelFiltroTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("reportes:cobros_descargar")
        self.client.force_login(self.admin)

    def con_comprobante(self, n):
        for i in range(n):
            c = self.cobro(self.tincho, "100.00")
            c.comprobante.save("x.pdf", SimpleUploadedFile("x.pdf", b"datos"))

    def mensajes(self, r):
        return [str(m) for m in get_messages(r.wsgi_request)]

    def test_max_zip_es_1000(self):
        self.assertEqual(servicios.MAX_ZIP, 1000)

    def test_justo_en_el_limite_descarga_zip(self):
        self.con_comprobante(3)
        with mock.patch.object(servicios, "MAX_ZIP", 3):
            r = self.client.get(self.url, {"formato": "zip"})
        self.assertEqual(r["Content-Type"], "application/zip")
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertRegex(r["Content-Disposition"], r"comprobantes-\d\d-\d\d-\d{4}\.zip")
        z = zipfile.ZipFile(BytesIO(b"".join(r.streaming_content)))
        self.assertEqual(len(z.namelist()), 3)

    def test_uno_arriba_del_limite_avisa_y_redirige_al_filtro(self):
        self.con_comprobante(4)
        with mock.patch.object(servicios, "MAX_ZIP", 3):
            r = self.client.get(self.url, {"formato": "zip", "estado": "finalizado"})
        self.assertRedirects(
            r, reverse("cobros:lista") + "?estado=finalizado", fetch_redirect_response=False
        )
        self.assertEqual(
            self.mensajes(r), ["Hay 4 cobros con este filtro. Elegí un rango con 3 o menos."]
        )

    def test_mensaje_con_punto_de_miles(self):
        with mock.patch("django.db.models.QuerySet.count", return_value=1206):
            r = self.client.get(self.url, {"formato": "zip"})
        self.assertEqual(
            self.mensajes(r),
            ["Hay 1.206 cobros con este filtro. Elegí un rango con 1.000 o menos."],
        )

    def test_cero_comprobantes_avisa(self):
        efectivo = Cuenta.objects.create(destinatario=self.martin, tipo="efectivo", titular="Caja")
        self.cobro(efectivo, "50.00", metodo="efectivo")
        r = self.client.get(self.url, {"formato": "zip"})
        self.assertRedirects(r, reverse("cobros:lista"), fetch_redirect_response=False)
        self.assertEqual(
            self.mensajes(r), ["No se encontraron comprobantes en los cobros elegidos."]
        )

    def test_zip_con_por_es_invalido(self):
        self.assertEqual(
            self.client.get(self.url, {"formato": "zip", "por": "item"}).status_code, 400
        )

    def test_zip_comprobantes_devuelve_archivo_en_posicion_cero(self):
        self.con_comprobante(1)
        archivo = servicios.zip_comprobantes(Cobro.objects.all())
        self.assertEqual(archivo.tell(), 0)
        self.assertEqual(len(zipfile.ZipFile(archivo).namelist()), 1)


class PorItemTest(DatosMartinO, SistemaTestCase):
    def test_monto_del_cobro_y_del_item_y_referencia(self):
        c = self.cobro(self.tincho, "3000.00")
        ItemCobro.objects.create(cobro=c, numero=1, id_prestacion=1, monto=D("1000.00"))
        ItemCobro.objects.create(cobro=c, numero=2, id_prestacion=2, monto=D("2000.00"))
        self.assertIn("Monto del cobro", servicios.TITULOS_ITEM)
        self.assertNotIn("Monto del cobro", servicios.TITULOS_COBRO)
        filas = list(servicios.filas_por_item(Cobro.objects.all(), url_comprobante=lambda c: ""))
        self.assertEqual(len(filas), 2)
        d = [dict(zip(servicios.TITULOS_ITEM, f, strict=True)) for f in filas]
        self.assertEqual([x["Monto del cobro"] for x in d], [D("3000.00")] * 2)
        self.assertEqual([x["Monto del ítem"] for x in d], [D("1000.00"), D("2000.00")])
        self.assertEqual([x["Número de ítem"] for x in d], [f"{c.pk}-1", f"{c.pk}-2"])

    def test_referencia_con_cobro_de_cuatro_cifras(self):
        c = self.cobro(self.tincho, "100.00")
        Cobro.objects.filter(pk=c.pk).update()  # el pk real no importa: se compara con `referencia`
        i = ItemCobro.objects.create(cobro=c, numero=12, id_prestacion=1, monto=D("100.00"))
        fila = next(servicios.filas_por_item(Cobro.objects.all(), url_comprobante=lambda c: ""))
        self.assertEqual(fila[22], f"{c.pk}-12")
        self.assertEqual(i.referencia, f"{c.pk}-12")

    def test_descarga_xlsx_por_item(self):
        self.client.force_login(self.admin)
        c = self.cobro(self.tincho, "3000.00")
        ItemCobro.objects.create(cobro=c, numero=1, id_prestacion=1, monto=D("1000.00"))
        r = self.client.get(
            reverse("reportes:cobros_descargar"), {"formato": "xlsx", "por": "item"}
        )
        hoja = load_workbook(BytesIO(r.content)).active
        self.assertEqual(hoja["E1"].value, "Monto del cobro")
