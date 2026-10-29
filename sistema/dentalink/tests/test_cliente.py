import json
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

from django.test import override_settings

from sistema.core.tests import SistemaTestCase
from sistema.dentalink import cliente

CONEXION = "sistema.dentalink.cliente.http.client.HTTPSConnection"


def _respuesta(status, cuerpo):
    r = MagicMock(status=status)
    r.read.return_value = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
    return r


@override_settings(DENTALINK_TOKEN="x")
class CrearPagoTest(SistemaTestCase):
    def _crear(self, status=200, cuerpo=None, connect=None, getresponse=None):
        with patch(CONEXION) as clase:
            conn = clase.return_value
            conn.connect.side_effect = connect
            if getresponse:
                conn.getresponse.side_effect = getresponse
            else:
                conn.getresponse.return_value = _respuesta(status, cuerpo or {})
            return cliente.crear_pago({"monto_pago": "1.00"})

    def test_connect_rechazado_es_reintentable(self):
        self.assertEqual(self._crear(connect=ConnectionRefusedError()).tipo, "reintentable")

    def test_timeout_al_leer_es_dudoso(self):
        self.assertEqual(self._crear(getresponse=TimeoutError()).tipo, "dudoso")

    def test_503_y_500_son_reintentables(self):
        self.assertEqual(self._crear(503).tipo, "reintentable")
        self.assertEqual(self._crear(500).tipo, "reintentable")

    def test_499_es_rechazado(self):
        self.assertEqual(self._crear(499).tipo, "rechazado")

    def test_400_guarda_el_cuerpo(self):
        r = self._crear(400, b"La cuenta ya pagada")
        self.assertEqual(r.tipo, "rechazado")
        self.assertIn("ya pagada", r.error)

    def test_rechazado_corta_el_error_en_200_caracteres_mas_el_prefijo(self):
        error = self._crear(400, b"a" * 501).error
        self.assertEqual(error, "Dentalink rechazó el pago: " + "a" * 200)

    def test_200_con_id_es_ok(self):
        r = self._crear(200, {"data": {"id": 77}})
        self.assertEqual((r.tipo, r.id_pago), ("ok", 77))

    def test_200_sin_id_es_sin_id(self):
        self.assertEqual(self._crear(200, {"data": {}}).tipo, "sin_id")

    def test_200_con_json_roto_es_sin_id(self):
        self.assertEqual(self._crear(200, b"no json").tipo, "sin_id")

    def test_usa_timeout_de_lectura_despues_de_conectar(self):
        with patch(CONEXION) as clase:
            clase.return_value.getresponse.return_value = _respuesta(200, {"data": {"id": 1}})
            cliente.crear_pago({})
        clase.assert_called_once_with(clase.call_args.args[0], timeout=cliente.TIMEOUT_CONECTAR)
        clase.return_value.sock.settimeout.assert_called_once_with(cliente.TIMEOUT_LEER)

    @override_settings(DENTALINK_TOKEN="")
    def test_sin_token_es_reintentable_sin_conexion(self):
        with patch(CONEXION) as clase:
            r = cliente.crear_pago({})
        self.assertEqual(r.tipo, "reintentable")
        clase.assert_not_called()


@override_settings(DENTALINK_TOKEN="x")
class LecturaClienteTest(SistemaTestCase):
    def _get(self, status=200, cuerpo=None, request=None):
        with patch(CONEXION) as clase:
            conn = clase.return_value
            if request:
                conn.request.side_effect = request
            conn.getresponse.return_value = _respuesta(
                status, cuerpo if cuerpo is not None else {"data": []}
            )
            resultado = cliente.buscar_pacientes("Juan")
            return resultado, conn

    def _q(self, texto):
        with patch(CONEXION) as clase:
            conn = clase.return_value
            conn.getresponse.return_value = _respuesta(200, {"data": []})
            cliente.buscar_pacientes(texto)
        url = conn.request.call_args_list[0].args[1]  # el pedido por nombre va primero (023/R7)
        return json.loads(unquote(url.split("q=")[1]))

    def test_dni_con_puntos_busca_por_rut(self):
        self.assertEqual(self._q("30.111.222"), {"rut": {"eq": "30111222"}})

    def test_nombre_busca_con_lk(self):
        self.assertEqual(self._q("Juan"), {"nombre": {"lk": "Juan"}})

    def test_devuelve_id_nombre_y_dni(self):
        with patch(CONEXION) as clase:
            clase.return_value.getresponse.return_value = _respuesta(
                200, {"data": [{"id": 501, "nombre": "Juan Paciente", "rut": "30111222"}]}
            )
            r = cliente.buscar_pacientes("Juan")
        self.assertEqual(
            r, [{"id": 501, "nombre": "Juan Paciente", "dni": "30111222", "nacimiento": None}]
        )

    def test_status_503_no_disponible(self):
        with self.assertRaises(cliente.DentalinkNoDisponible):
            self._get(503)

    def test_status_300_y_199_no_disponible(self):
        for status in (199, 300):
            with self.assertRaises(cliente.DentalinkNoDisponible):
                self._get(status)

    def test_timeout_no_disponible(self):
        with self.assertRaises(cliente.DentalinkNoDisponible):
            self._get(request=TimeoutError())

    def test_json_roto_no_disponible(self):
        with self.assertRaises(cliente.DentalinkNoDisponible):
            self._get(cuerpo=b"<html>")

    def test_sin_clave_data_no_disponible(self):
        with self.assertRaises(cliente.DentalinkNoDisponible):
            self._get(cuerpo={"otra": 1})

    @override_settings(DENTALINK_TOKEN="")
    def test_sin_token_no_disponible_sin_conexion(self):
        with patch(CONEXION) as clase, self.assertRaises(cliente.DentalinkNoDisponible):
            cliente.listar("bancos")
        clase.assert_not_called()

    def test_lectura_usa_timeout_de_3_segundos(self):
        with patch(CONEXION) as clase:
            clase.return_value.getresponse.return_value = _respuesta(200, {"data": []})
            cliente.listar("bancos")
        self.assertEqual(clase.call_args.kwargs["timeout"], 3)

    def test_total_y_pagado_son_decimal(self):
        respuestas = [
            _respuesta(200, {"data": [{"id": 1, "nombre": "T", "id_dentista": 11}]}),
            _respuesta(
                200,
                b'{"data": [{"id": 5, "id_prestacion": 9001, "total": 100000, "pagado": 60000.5}]}',
            ),
        ]
        with patch(CONEXION) as clase:
            clase.return_value.getresponse.side_effect = respuestas
            t = cliente.tratamientos(501)
        p = t[0]["prestaciones"][0]
        self.assertEqual((p["total"], p["pagado"]), (Decimal("100000"), Decimal("60000.5")))
        self.assertIsInstance(p["pagado"], Decimal)

    def test_listar_devuelve_id_y_nombre(self):
        with patch(CONEXION) as clase:
            clase.return_value.getresponse.return_value = _respuesta(
                200, {"data": [{"id": 20, "nombre": "Banco Inventado", "x": 1}]}
            )
            self.assertEqual(cliente.listar("bancos"), [{"id": 20, "nombre": "Banco Inventado"}])


@override_settings(DENTALINK_TOKEN="x")
class PacienteClienteTest(SistemaTestCase):
    def test_paciente_devuelve_id_nombre_y_dni(self):
        with patch(CONEXION) as clase:
            clase.return_value.getresponse.return_value = _respuesta(
                200, {"data": {"id": 501, "nombre": "Juan Paciente", "rut": "30111222"}}
            )
            r = cliente.paciente(501)
        self.assertEqual(
            r, {"id": 501, "nombre": "Juan Paciente", "dni": "30111222", "nacimiento": None}
        )
        self.assertEqual(
            clase.return_value.request.call_args.args[1].split("/")[-2:], ["pacientes", "501"]
        )

    def test_paciente_sin_rut_devuelve_dni_vacio(self):
        with patch(CONEXION) as clase:
            clase.return_value.getresponse.return_value = _respuesta(
                200, {"data": {"id": 501, "nombre": "Juan Paciente"}}
            )
            self.assertEqual(cliente.paciente(501)["dni"], "")

    def test_paciente_con_falla_de_red_no_disponible(self):
        with patch(CONEXION) as clase, self.assertRaises(cliente.DentalinkNoDisponible):
            clase.return_value.request.side_effect = TimeoutError()
            cliente.paciente(501)
