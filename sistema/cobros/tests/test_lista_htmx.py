from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.arca.tests.fabricas import factura_prueba
from sistema.cobros.models import Cobro
from sistema.core.tests import SistemaTestCase
from sistema.reportes.tests import DatosMartinO

LISTA = reverse("cobros:lista")
HTMX = {"HTTP_HX_REQUEST": "true"}


class ListaHtmxTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.operador = get_user_model().objects.create_user(
            "operadora", email="o@example.com", role="operator"
        )
        for i in range(120):  # 7 Pendiente y 113 Finalizado; nombres falsos
            self.cobro(
                self.tincho,
                "1000.00",
                estado="pendiente" if i < 7 else "finalizado",
                nombre_paciente=f"Paciente Prueba {i}",
                creado_por=self.operador,
            )
        self.client.force_login(self.admin)

    def filas(self, respuesta):
        return respuesta.content.decode().count('name="pk"') // 2  # tabla + tarjeta (020)

    def test_filtro_por_estado_deja_las_7_pendientes(self):
        r = self.client.get(LISTA, {"estado": "pendiente"}, **HTMX)
        self.assertEqual(self.filas(r), 7)

    def test_ultima_pagina_tiene_20_filas(self):
        self.assertEqual(self.filas(self.client.get(LISTA, {"page": 3})), 20)

    def test_la_respuesta_varia_por_hx_request(self):
        self.assertIn("HX-Request", self.client.get(LISTA, **HTMX)["Vary"])
        self.assertIn("HX-Request", self.client.get(LISTA)["Vary"])

    def test_enlace_viejo_con_estado_y_pagina_2_responde_200(self):
        r = self.client.get(LISTA, {"estado": "pendiente", "page": 2})
        self.assertEqual(r.status_code, 200)

    def test_filtro_factura_con_error_deja_solo_ese_cobro(self):
        cobro = Cobro.objects.get(nombre_paciente="Paciente Prueba 12")
        factura_prueba(cobro, estado="error", motivo="x")
        r = self.client.get(LISTA, {"factura_con_error": "on"})
        self.assertEqual(self.filas(r), 1)

    def test_operador_no_ve_casillas_ni_marcas(self):
        self.client.force_login(self.operador)
        r = self.client.get(LISTA)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'name="pk"')
        self.assertNotContains(r, "Marcas")

    def test_operador_ignora_los_filtros_del_administrador(self):
        self.client.force_login(self.operador)
        r = self.client.get(LISTA, {"metodo": "efectivo", "cuenta": "1"})
        self.assertContains(r, "Página 1 de 3")
