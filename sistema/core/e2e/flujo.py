"""Base de los E2E de flujos críticos (solo 1280 px). Datos ficticios; Dentalink con dobles."""

import re
from datetime import datetime, timedelta
from io import BytesIO
from unittest import mock

from PIL import Image

from sistema.cobros.models import Cobro
from sistema.cobros.tests.test_efectivo import DELAY, PACIENTE, TRATAMIENTOS
from sistema.core.e2e.base import E2ECase, elegir
from sistema.core.tiempo import ARG
from sistema.dentalink.models import Banco
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento
from sistema.destinatarios.models import Cuenta, Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.servicios import abrir_sesion, crear_terminal
from sistema.usuarios import roles

NUEVO = "/sistema/cobros/nuevo/?paciente=501"
DETALLE = re.compile(r"/sistema/cobros/(\d+)/$")
ASIGNADO = re.compile(r"/sistema/cobros/nuevo/\?cobro=(\d+)$")  # 024/FR-014


def comprobante(nombre="c.png"):
    """Imagen mínima para subir como comprobante."""
    salida = BytesIO()
    Image.new("RGB", (40, 30), "white").save(salida, format="PNG")
    return {"name": nombre, "mimeType": "image/png", "buffer": salida.getvalue()}


class FlujoCase(E2ECase):
    def setUp(self):
        super().setUp()
        prestaciones = [prestacion(1, "50000", "0"), prestacion(2, "30000", "0")]
        for nombre, valor in (
            (TRATAMIENTOS, [tratamiento(prestaciones)]),
            (PACIENTE, {"id": 501, "nombre": "Ana Ejemplo", "dni": "30111222"}),
            (DELAY, None),
        ):
            parche = mock.patch(nombre, return_value=valor)
            parche.start()
            self.addCleanup(parche.stop)
        self.destinatario = Destinatario.objects.create(
            nombre="Destinatario Ejemplo", tipo="clinica"
        )
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.destinatario,
            alias="ejemplo.uno",
            tope_mensual=100_000_000,
        )
        self.efectivo = Cuenta.objects.create(destinatario=self.destinatario, tipo="efectivo")
        self.proveedor, _ = Proveedor.objects.get_or_create(nombre="Mercadopago")
        Banco.objects.get_or_create(id_dentalink=777, defaults={"nombre": "Banco Ejemplo"})

    def terminal_con_sesion(self, **metodos):
        """Terminal que acepta `metodos` (acepta_debito=True...) con una sesión abierta."""
        cuenta = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=self.destinatario,
            titular="Titular Ejemplo",
            proveedor=self.proveedor,
            objetivo=300000,
        )
        terminal = crear_terminal(
            usuario=None, nombre="Terminal Ejemplo", proveedor=self.proveedor, **metodos
        )
        abrir_sesion(terminal, cuenta, datetime.now(ARG) - timedelta(hours=1), usuario=None)
        return terminal, cuenta

    def pagina_nuevo(self, rol=roles.OPERATOR, ruta=NUEVO):
        p = self.nueva_pagina(rol)
        p.goto(self.url(ruta))
        p.wait_for_load_state("networkidle")
        p.wait_for_function("document.querySelector('[x-data]')._x_dataStack")
        return p

    def cobrar(self, metodo, monto="10.000,00", pos=None):
        """Llena el formulario de nuevo cobro, confirma el diálogo y devuelve (página, Cobro).

        `pos`: {"neto": ..., "operacion": ...} para los métodos con terminal."""
        p = self.pagina_nuevo()
        p.locator("[data-rama] .tc-cab .tc-chev").first.click()  # 024/FR-003: arrancan cerrados
        p.check("input[name=elegida_1]")
        p.fill("input[name=monto_1]", monto)
        p.locator(f"label:has(input[value={metodo}])").click()
        if pos:  # una sola terminal acepta el método: queda elegida sola (022/FR-004)
            p.fill("#id_monto_neto", pos["neto"])
            p.fill("#id_numero_operacion", pos["operacion"])
            if metodo in ("debito", "credito"):
                elegir(p, "#id_banco_emisor", label="Banco Ejemplo")
                p.fill("#id_ultimos_4", "4321")
            p.set_input_files("#id_comprobante", files=[comprobante()])
        # 024/FR-013: con transferencia el botón es "Buscar destinatario", en la caja del método
        p.locator("[data-buscar]" if metodo == "transferencia" else "#guardar-cobro").click()
        p.wait_for_selector("#resumen-cobro[open]")
        p.locator("#resumen-cobro").get_by_role("button", name="Confirmar").click()
        destino = ASIGNADO if metodo == "transferencia" else DETALLE
        p.wait_for_url(destino)
        return p, Cobro.objects.get(pk=destino.search(p.url).group(1))
