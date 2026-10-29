from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from sistema.arca import servicios as arca
from sistema.arca.tests.fabricas import (
    AHORA,
    ArcaFalso,
    cobro_facturable,
    emisor_prueba,
    factura_prueba,
)
from sistema.cobros.models import Cobro
from sistema.core.tests import SistemaTestCase
from sistema.reportes.filtros import FiltroCobros
from sistema.reportes.tests import DatosMartinO


class FiltrosFacturaTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()
        self.con_error = self.cobro_con(estado="error", motivo="10016: dato inválido")
        self.a_revisar = self.cobro_con(estado="pendiente", a_revisar=True)
        self.emitido = self.cobro_con(estado="pendiente")  # se emite abajo
        ArcaFalso(self)
        with mock.patch("django.utils.timezone.now", return_value=AHORA):
            self.assertEqual(arca.emitir(self.emitido.comprobantes_fiscales.get().pk), "emitido")

    def cobro_con(self, **campos):
        cobro = cobro_facturable()
        factura_prueba(cobro, emisor=self.emisor, **campos)
        return cobro

    def filtrar(self, **datos):
        filtro = FiltroCobros({k: "on" for k in datos}, queryset=Cobro.objects.all())
        return set(filtro.qs.values_list("pk", flat=True))

    def test_factura_con_error_deja_solo_el_cobro_con_error(self):
        self.assertEqual(self.filtrar(factura_con_error=1), {self.con_error.pk})

    def test_factura_a_revisar_deja_solo_el_cobro_a_revisar(self):
        self.assertEqual(self.filtrar(factura_a_revisar=1), {self.a_revisar.pk})

    def test_los_dos_filtros_juntos_no_dejan_ninguno(self):
        self.assertEqual(self.filtrar(factura_con_error=1, factura_a_revisar=1), set())

    def test_sin_filtro_estan_los_tres(self):
        self.assertEqual(len(self.filtrar()), 3)

    def test_a_revisar_solo_cuenta_la_factura_vigente(self):
        cobro = cobro_facturable()
        factura_prueba(
            cobro, emisor=self.emisor, estado="descartado", vigente=False, a_revisar=True
        )
        self.assertNotIn(cobro.pk, self.filtrar(factura_a_revisar=1))

    def test_un_cobro_con_dos_comprobantes_en_error_sale_una_sola_vez(self):
        cobro = self.con_error
        factura_prueba(cobro, emisor=self.emisor, estado="error", motivo="otro", vigente=False)
        filtro = FiltroCobros({"factura_con_error": "on"}, queryset=Cobro.objects.all())
        lista = list(filtro.qs.values_list("pk", flat=True))
        self.assertEqual(lista, [cobro.pk])

    def test_marcas_del_cobro(self):
        self.assertEqual(arca.marcas(self.con_error), ["Factura con error"])
        self.assertEqual(arca.marcas(self.a_revisar), ["Factura a revisar"])
        self.assertEqual(arca.marcas(self.emitido), [])


class DescargaMarcasTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()
        self.client.force_login(self.admin)

    def con_error(self, cuantos):
        for _ in range(cuantos):
            cobro = self.cobro(self.tincho, "100.00")
            factura_prueba(cobro, emisor=self.emisor, estado="error", motivo="rechazo")

    def descargar(self):
        url = reverse("reportes:cobros_descargar")
        return self.client.get(url, {"formato": "csv", "por": "cobro"})

    def test_la_descarga_por_cobro_trae_la_marca_factura_con_error(self):
        self.con_error(1)
        self.assertIn(
            "Factura con error", b"".join(self.descargar().streaming_content).decode("utf-8-sig")
        )

    def consultas(self):
        with CaptureQueriesContext(connection) as c:
            self.descargar()
        return len(c)

    def test_con_50_cobros_la_descarga_hace_las_mismas_consultas_que_con_1(self):
        self.con_error(1)
        con_uno = self.consultas()
        self.con_error(49)
        self.assertEqual(self.consultas(), con_uno)
