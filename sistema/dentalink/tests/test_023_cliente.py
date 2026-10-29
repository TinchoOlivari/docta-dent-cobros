"""023 Fase 0: datos del paciente y búsqueda por nombre o apellidos (FR-033, research R7)."""

import json
from datetime import date
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

from django.test import override_settings

from sistema.core.tests import SistemaTestCase
from sistema.dentalink import cliente, servicios

_GET = "sistema.dentalink.cliente._get"
JUANA = {
    "id": 501,
    "nombre": "Juana",
    "apellidos": "Ejemplo Prueba",
    "rut": "30111222",
    "fecha_nacimiento": "1980-03-05",
}


class DatosPacienteTest(SistemaTestCase):
    def _paciente(self, **cambios):
        with patch(_GET, return_value={**JUANA, **cambios}):
            return cliente.paciente(501)

    def test_nombre_con_apellidos_dni_y_nacimiento(self):
        self.assertEqual(
            self._paciente(),
            {
                "id": 501,
                "nombre": "Juana Ejemplo Prueba",
                "dni": "30111222",
                "nacimiento": date(1980, 3, 5),
            },
        )

    def test_rut_vacio_da_dni_vacio(self):
        self.assertEqual(self._paciente(rut="")["dni"], "")
        self.assertEqual(self._paciente(rut=None)["dni"], "")

    def test_sin_apellidos_el_nombre_queda_sin_espacios_de_mas(self):
        self.assertEqual(self._paciente(apellidos=None)["nombre"], "Juana")
        self.assertEqual(self._paciente(apellidos="")["nombre"], "Juana")

    def test_fecha_de_nacimiento_mal_formada_o_vacia_da_none(self):
        for valor in ("x", "", None, "1980-13-45"):
            with self.subTest(valor=valor):
                self.assertIsNone(self._paciente(fecha_nacimiento=valor)["nacimiento"])

    def test_nacimiento_paciente_con_dentalink_caido_da_none(self):
        with patch(_GET, side_effect=cliente.DentalinkNoDisponible()):
            self.assertIsNone(servicios.nacimiento_paciente(501))

    def test_nacimiento_paciente_devuelve_la_fecha(self):
        with patch(_GET, return_value=JUANA):
            self.assertEqual(servicios.nacimiento_paciente(501), date(1980, 3, 5))


@override_settings(DENTALINK_TOKEN="x")
class BusquedaPacientesTest(SistemaTestCase):
    def _buscar(self, texto, respuestas):
        """`respuestas`: un cuerpo `data` por pedido, en orden. Devuelve (resultado, filtros q)."""
        with patch("sistema.dentalink.cliente.http.client.HTTPSConnection") as clase:
            conn = clase.return_value
            conn.getresponse.side_effect = [
                MagicMock(status=200, read=MagicMock(return_value=json.dumps({"data": d}).encode()))
                for d in respuestas
            ]
            resultado = cliente.buscar_pacientes(texto)
        filtros = [
            json.loads(unquote(c.args[1].split("q=")[1])) for c in conn.request.call_args_list
        ]
        return resultado, filtros

    def test_texto_hace_2_pedidos_por_nombre_y_por_apellidos(self):
        resultado, filtros = self._buscar("Prueba", [[], [JUANA]])
        self.assertEqual(filtros, [{"nombre": {"lk": "Prueba"}}, {"apellidos": {"lk": "Prueba"}}])
        self.assertEqual([p["nombre"] for p in resultado], ["Juana Ejemplo Prueba"])

    def test_el_mismo_paciente_en_los_2_pedidos_sale_una_vez(self):
        resultado, _ = self._buscar("Ejemplo", [[JUANA], [JUANA]])
        self.assertEqual(len(resultado), 1)

    def test_primero_los_de_nombre_y_despues_los_de_apellidos(self):
        otro = {**JUANA, "id": 502, "nombre": "Pedro"}
        resultado, _ = self._buscar("Ejemplo", [[otro], [JUANA, otro]])
        self.assertEqual([p["id"] for p in resultado], [502, 501])

    def test_dni_con_puntos_hace_1_pedido_por_rut(self):
        _, filtros = self._buscar("30.111.222", [[JUANA]])
        self.assertEqual(filtros, [{"rut": {"eq": "30111222"}}])

    def test_si_falla_uno_de_los_2_pedidos_no_hay_servicio(self):
        with patch(_GET, side_effect=[[], cliente.DentalinkNoDisponible()]):
            with self.assertRaises(cliente.DentalinkNoDisponible):
                cliente.buscar_pacientes("Prueba")


class TratamientosEnParaleloTest(SistemaTestCase):
    """Los detalles se piden en paralelo y salen en el orden de la lista de tratamientos."""

    @staticmethod
    def _falso(falla=None):
        def get(ruta, params=None):
            if ruta.endswith("/tratamientos"):
                return [{"id": i, "nombre": f"T{i}"} for i in range(1, 11)]
            id_ = int(ruta.split("/")[2])
            if id_ == falla:
                raise cliente.DentalinkNoDisponible()
            return [{"id": id_ * 100, "total": 10, "pagado": 0}]

        return get

    def test_conserva_el_orden_con_10_tratamientos(self):
        with patch(_GET, side_effect=self._falso()) as get:
            t = cliente.tratamientos(501)
        self.assertEqual([x["id"] for x in t], list(range(1, 11)))
        self.assertEqual([x["prestaciones"][0]["id"] for x in t], [i * 100 for i in range(1, 11)])
        self.assertEqual(get.call_count, 11)

    def test_si_falla_un_detalle_no_hay_servicio(self):
        with patch(_GET, side_effect=self._falso(falla=7)):
            with self.assertRaises(cliente.DentalinkNoDisponible):
                cliente.tratamientos(501)
