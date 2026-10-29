"""Flujo crítico: un cobro con facturación activa emite factura; el administrador pide la nota."""

from decimal import Decimal as D

from sistema.arca.models import ComprobanteFiscal
from sistema.arca.tests.fabricas import CAE, ArcaFalso, emisor_prueba
from sistema.core.e2e.flujo import FlujoCase
from sistema.destinatarios.models import Destinatario
from sistema.usuarios import roles


class FlujoFacturacionE2E(FlujoCase):
    def test_cobro_emite_factura_y_el_administrador_emite_la_nota_de_credito(self):
        self.arca = ArcaFalso(self)  # ARCA autoriza todo; el servidor corre en este proceso
        emisor = emisor_prueba()
        Destinatario.objects.filter(pk=self.destinatario.pk).update(
            facturacion_activa=True, emisor=emisor
        )
        _, cobro = self.cobrar("efectivo")

        factura = ComprobanteFiscal.objects.get(cobro=cobro, tipo="factura")
        self.assertEqual(factura.estado, ComprobanteFiscal.Estado.EMITIDO)
        self.assertEqual(factura.cae, CAE)
        self.assertEqual(factura.total, D("10000.00"))
        self.assertEqual(factura.emisor, emisor)
        self.assertTrue(factura.vigente)

        p = self.nueva_pagina(roles.ADMIN)
        p.goto(self.url(f"/sistema/cobros/{cobro.pk}/"))
        p.get_by_role("button", name="Emitir nota de crédito").first.click()
        p.locator(f"dialog#confirmar-nota-{factura.pk}").get_by_role(
            "button", name="Emitir nota de crédito"
        ).click()
        p.wait_for_selector("text=Nota de crédito pedida.")

        nota = ComprobanteFiscal.objects.get(cobro=cobro, tipo="nota_credito")
        self.assertEqual(nota.estado, ComprobanteFiscal.Estado.EMITIDO)
        self.assertEqual(nota.total, D("10000.00"))
        factura.refresh_from_db()
        self.assertFalse(factura.vigente)
