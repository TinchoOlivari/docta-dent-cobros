"""Flujo crítico: el administrador registra un pago complementario."""

from decimal import Decimal as D

from sistema.cobros.models import PagoComplementario
from sistema.core.e2e.base import elegir
from sistema.core.e2e.flujo import FlujoCase, comprobante
from sistema.usuarios import roles


class FlujoComplementarioE2E(FlujoCase):
    def test_pago_complementario_por_transferencia_queda_registrado_en_la_cuenta(self):
        p = self.nueva_pagina(roles.ADMIN)
        p.goto(self.url("/sistema/cobros/complementarios/nuevo/"))
        p.wait_for_load_state("networkidle")
        p.wait_for_function("document.querySelector('#bloque-cuenta')._x_dataStack")
        elegir(p, "select[name=destinatario]", str(self.destinatario.pk))
        p.fill("#id_monto", "25.000,50")
        elegir(p, "select[name=metodo]", "transferencia")
        p.set_input_files("#id_comprobante", files=[comprobante()])
        p.get_by_role("button", name="Guardar").click()
        p.wait_for_selector("text=Pago complementario registrado.")

        pago = PagoComplementario.objects.get()
        self.assertEqual(pago.estado, PagoComplementario.Estado.REGISTRADO)
        self.assertEqual(pago.monto, D("25000.50"))
        self.assertEqual(pago.metodo, "transferencia")
        self.assertEqual(pago.destinatario, self.destinatario)
        self.assertEqual(pago.cuenta, self.cuenta)  # la única cuenta del destinatario
        self.assertTrue(pago.comprobante)
        self.assertEqual(pago.creado_por.role, roles.ADMIN)
