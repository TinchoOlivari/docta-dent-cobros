"""Flujo crítico: el administrador reabre un cobro finalizado y después lo cancela."""

from sistema.cobros.models import Cobro
from sistema.core.e2e.base import elegir
from sistema.core.e2e.flujo import FlujoCase, comprobante
from sistema.usuarios import roles


class FlujoEstadosE2E(FlujoCase):
    def test_reabrir_un_cobro_finalizado_y_cancelarlo(self):
        _, cobro = self.cobrar("transferencia")
        op = self.nueva_pagina(roles.OPERATOR)
        op.goto(self.url(f"/sistema/cobros/{cobro.pk}/"))
        op.set_input_files("#id_comprobante_adjunto", files=[comprobante()])
        op.get_by_role("button", name="Guardar cobro").click()
        op.wait_for_selector("text=Comprobante guardado.")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, Cobro.Estado.FINALIZADO)

        p = self.nueva_pagina(roles.ADMIN)
        p.goto(self.url(f"/sistema/cobros/{cobro.pk}/"))
        p.get_by_text("Más acciones").click()
        p.fill("#reabrir-motivo", "Motivo de prueba")
        p.get_by_role("button", name="Reabrir", exact=True).click()
        p.locator("#confirmar-reabrir").get_by_role("button", name="Reabrir").click()
        p.wait_for_selector("text=Cobro reabierto.")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, Cobro.Estado.PENDIENTE)
        self.assertEqual(cobro.cuenta, self.cuenta)

        p.get_by_text("Más acciones").click()
        elegir(p, "#cancelar-motivo", "no_pago")
        p.get_by_role("button", name="Cancelar cobro", exact=True).click()
        p.locator("#confirmar-cancelar").get_by_role("button", name="Cancelar cobro").click()
        p.wait_for_selector("text=Cobro cancelado.")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, Cobro.Estado.CANCELADO)
        self.assertEqual(cobro.motivo_cancelacion, "no_pago")
