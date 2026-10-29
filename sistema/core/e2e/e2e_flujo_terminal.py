"""Flujo crítico: el administrador abre y cierra una sesión de terminal."""

from datetime import datetime

from sistema.core.e2e.base import elegir
from sistema.core.e2e.flujo import FlujoCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.models import Sesion
from sistema.terminales.servicios import crear_terminal
from sistema.usuarios import roles


class FlujoTerminalE2E(FlujoCase):
    def test_abrir_y_cerrar_una_sesion(self):
        cuenta = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=self.destinatario,
            titular="Titular Ejemplo",
            proveedor=self.proveedor,
            objetivo=300000,
        )
        terminal = crear_terminal(
            usuario=None, nombre="Terminal Ejemplo", proveedor=self.proveedor, acepta_qr=True
        )
        p = self.nueva_pagina(roles.ADMIN)
        p.goto(self.url(f"/sistema/terminales/{terminal.pk}/"))
        p.wait_for_load_state("networkidle")
        elegir(p, "#id_cuenta", str(cuenta.pk))
        p.fill("#id_inicio_0", f"{datetime.now(ARG):%d/%m/%Y}")
        p.fill("#id_inicio_1", "00:00")
        p.get_by_role("button", name="Abrir", exact=True).click()
        p.wait_for_selector("text=Sesión abierta.")
        sesion = Sesion.objects.get(terminal=terminal)
        self.assertEqual(sesion.cuenta, cuenta)
        self.assertIsNone(sesion.fin)

        p.locator(f"#cerrar-{sesion.pk}").get_by_role("button", name="Cerrar").click()
        p.locator(f"#confirmar-cerrar-{sesion.pk}").get_by_role("button", name="Cerrar").click()
        p.wait_for_selector("text=Sesión cerrada.")
        sesion.refresh_from_db()
        self.assertIsNotNone(sesion.fin)
        self.assertGreater(sesion.fin, sesion.inicio)
