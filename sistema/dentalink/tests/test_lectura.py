from decimal import Decimal
from unittest.mock import patch

from sistema.core.tests import SistemaTestCase
from sistema.dentalink import servicios
from sistema.dentalink.models import ConfiguracionDentalink

DRA_X = {"id": 11, "nombre": "Dra. X"}
DR_Y = {"id": 12, "nombre": "Dr. Y"}


def prestacion(id_det, total, pagado, *, id_prestacion=None, id_profesional=None):
    return {
        "id": id_det,
        "id_prestacion": id_prestacion or id_det,
        "nombre": f"Prestación {id_det}",
        "id_profesional": id_profesional,
        "total": Decimal(total),
        "pagado": Decimal(pagado),
    }


def tratamiento(prestaciones, *, id_profesional=12, profesionales=(DR_Y,)):
    return {
        "id": 1,
        "nombre": "Ortodoncia",
        "id_profesional": id_profesional,
        "profesionales": list(profesionales),
        "prestaciones": prestaciones,
    }


@patch("sistema.dentalink.servicios.cliente")
class SaldoTest(SistemaTestCase):
    def prestaciones(self, cliente, *prestaciones, **kw):
        cliente.tratamientos.return_value = [tratamiento(list(prestaciones), **kw)]
        return [p for t in servicios.prestaciones_de(501) for p in t["prestaciones"]]

    def test_saldo_es_total_menos_pagado(self, cliente):
        res = self.prestaciones(cliente, prestacion(9001, "100000.00", "60000.00"))
        self.assertEqual(res[0]["saldo"], Decimal("40000.00"))

    def test_pagada_del_todo_no_aparece(self, cliente):
        self.assertEqual(self.prestaciones(cliente, prestacion(9001, "100000.00", "100000.00")), [])

    def test_saldo_de_un_centavo_aparece(self, cliente):
        res = self.prestaciones(cliente, prestacion(9001, "100000.00", "99999.99"))
        self.assertEqual(res[0]["saldo"], Decimal("0.01"))

    def test_saldo_negativo_no_aparece(self, cliente):
        self.assertEqual(self.prestaciones(cliente, prestacion(9001, "100000.00", "100000.01")), [])

    def test_de_dos_prestaciones_solo_aparece_la_que_tiene_saldo(self, cliente):
        res = self.prestaciones(
            cliente,
            prestacion(9001, "100000.00", "100000.00"),
            prestacion(9002, "50000.00", "0.00"),
        )
        self.assertEqual([p["id"] for p in res], [9002])

    def test_tratamiento_sin_prestaciones_para_cobrar_no_se_devuelve(self, cliente):
        cliente.tratamientos.return_value = [tratamiento([prestacion(9001, "10.00", "10.00")])]
        self.assertEqual(servicios.prestaciones_de(501), [])


@patch("sistema.dentalink.servicios.cliente")
class ProfesionalTest(SistemaTestCase):
    def test_propone_el_de_la_prestacion(self, cliente):
        cliente.tratamientos.return_value = [
            tratamiento(
                [prestacion(9001, "10.00", "0.00", id_profesional=11)],
                profesionales=(DR_Y, DRA_X),
            )
        ]
        p = servicios.prestaciones_de(501)[0]["prestaciones"][0]
        self.assertEqual(p["profesional_propuesto"]["nombre"], "Dra. X")

    def test_sin_profesional_propone_el_del_tratamiento(self, cliente):
        cliente.tratamientos.return_value = [
            tratamiento([prestacion(9001, "10.00", "0.00")], profesionales=(DR_Y, DRA_X))
        ]
        p = servicios.prestaciones_de(501)[0]["prestaciones"][0]
        self.assertEqual(p["profesional_propuesto"]["nombre"], "Dr. Y")
        self.assertEqual([x["nombre"] for x in p["profesionales"]], ["Dr. Y", "Dra. X"])

    def test_profesionales_sin_repetir(self, cliente):
        cliente.tratamientos.return_value = [
            tratamiento([prestacion(9001, "10.00", "0.00")], profesionales=(DR_Y, DR_Y, DRA_X))
        ]
        p = servicios.prestaciones_de(501)[0]["prestaciones"][0]
        self.assertEqual([x["id"] for x in p["profesionales"]], [12, 11])


@patch("sistema.dentalink.servicios.cliente")
class SaldoAFavorYBusquedaTest(SistemaTestCase):
    def datos(self, cliente):
        cliente.tratamientos.return_value = [
            tratamiento([prestacion(9050, "0.00", "0.00", id_prestacion=9100)])
        ]

    def test_saldo_a_favor_configurado_aparece_con_saldo_cero(self, cliente):
        ConfiguracionDentalink.objects.create(
            pk=1, id_prestacion_saldo_a_favor=9100, nombre_prestacion_saldo_a_favor="Saldo a favor"
        )
        self.datos(cliente)
        p = servicios.prestaciones_de(501)[0]["prestaciones"][0]
        self.assertTrue(p["saldo_a_favor"])
        self.assertEqual(p["saldo"], Decimal("0.00"))

    def test_sin_configuracion_no_aparece(self, cliente):
        self.datos(cliente)
        self.assertEqual(servicios.prestaciones_de(501), [])

    def test_texto_en_blanco_devuelve_lista_vacia_sin_llamar_al_cliente(self, cliente):
        self.assertEqual(servicios.buscar_pacientes("  "), [])
        cliente.buscar_pacientes.assert_not_called()

    def test_texto_se_recorta_antes_de_buscar(self, cliente):
        servicios.buscar_pacientes(" Juan ")
        cliente.buscar_pacientes.assert_called_once_with("Juan")
