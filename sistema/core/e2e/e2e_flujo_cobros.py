"""Flujos críticos de cobro, un test por medio de pago. El resultado se verifica en la base."""

from decimal import Decimal as D

from sistema.cobros.models import Cobro
from sistema.core.e2e.flujo import FlujoCase, comprobante

POS = {"neto": "9.650,00", "operacion": "op-123456"}


class FlujoCobrosE2E(FlujoCase):
    def verificar_pos(self, cobro, metodo, cuenta):
        self.assertEqual(cobro.metodo, metodo)
        self.assertEqual(cobro.estado, Cobro.Estado.FINALIZADO)
        self.assertEqual(cobro.monto, D("10000.00"))
        self.assertEqual(cobro.monto_neto, D("9650.00"))
        self.assertEqual(cobro.numero_operacion, "OP-123456")
        self.assertEqual(cobro.cuenta, cuenta)
        self.assertEqual(cobro.terminal.nombre, "Terminal Ejemplo")
        self.assertTrue(cobro.comprobante)

    def test_transferencia_pendiente_hasta_subir_el_comprobante(self):
        p, cobro = self.cobrar("transferencia")
        self.assertEqual(cobro.estado, Cobro.Estado.PENDIENTE)
        self.assertEqual(cobro.monto, D("10000.00"))
        self.assertEqual(cobro.cuenta, self.cuenta)
        self.assertEqual(cobro.destinatario, self.destinatario)
        self.assertFalse(cobro.comprobante)

        # 024/FR-016 y FR-017: la cuenta y el comprobante en la misma pantalla
        p.wait_for_selector("text=Destinatario asignado")
        p.screenshot(path="e2e-capturas/024-asignado-1280.png", full_page=True)
        p.set_input_files("#id_comprobante", files=[comprobante()])
        p.locator("#guardar-cobro").click()
        p.wait_for_selector("text=Cobro guardado.")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, Cobro.Estado.FINALIZADO)
        self.assertTrue(cobro.comprobante)
        self.assertEqual(cobro.cuenta, self.cuenta)

    def test_captura_de_nuevo_cobro_con_debito(self):
        """024: captura de la vista para revisarla a ojo (1280 px)."""
        self.terminal_con_sesion(acepta_debito=True)
        p = self.pagina_nuevo()
        p.locator("[data-rama] .tc-cab .tc-chev").first.click()
        p.check("input[name=elegida_1]")
        p.locator("label:has(input[value=debito])").click()
        p.fill("#id_monto_neto", "9.650,00")
        p.set_input_files("#id_comprobante", files=[comprobante()])
        p.screenshot(path="e2e-capturas/024-debito-1280.png", full_page=True)
        p.locator(".caja-metodo").screenshot(path="e2e-capturas/024-caja-debito-1280.png")

    def test_la_casilla_del_tratamiento_marca_todas_con_su_saldo(self):
        """024/FR-004."""
        p = self.pagina_nuevo()
        errores = []
        p.on("pageerror", lambda e: errores.append(str(e)))
        p.locator("[data-marcar-rama]").first.click()
        self.assertEqual(errores, [])
        self.assertTrue(p.locator("input[name=elegida_1]").is_checked())
        self.assertTrue(p.locator("input[name=elegida_2]").is_checked())
        self.assertTrue(p.locator("[data-marcar-rama]").first.is_checked())
        self.assertEqual(p.locator("input[name=monto_1]").input_value(), "50.000,00")

    def test_el_tooltip_del_error_de_monto_se_ve_con_el_mouse_encima(self):
        """024/FR-005: el ícono no se mueve con el hover y muestra el texto."""
        p = self.pagina_nuevo()
        p.locator("[data-rama] .tc-cab .tc-chev").first.click()
        p.check("input[name=elegida_1]")
        p.fill("input[name=monto_1]", "99.999.999,00")
        p.locator("input[name=monto_1]").blur()
        icono = p.locator("[data-monto-error]").first
        antes = icono.bounding_box()
        icono.hover()
        self.assertEqual(icono.bounding_box(), antes)
        texto = icono.evaluate("e => getComputedStyle(e, '::after').content")
        self.assertEqual(texto, '"Monto excedido"')
        p.locator("input[name=monto_1]").hover()
        self.assertEqual(icono.evaluate("e => getComputedStyle(e, '::after').content"), "none")
        p.locator("[data-rama]").first.screenshot(path="e2e-capturas/024-tooltip-1280.png")

    def test_el_icono_de_pago_parcial_dice_cuanto_se_cobra(self):
        """024/FR-007."""
        p = self.pagina_nuevo()
        p.locator("[data-rama] .tc-cab .tc-chev").first.click()
        p.check("input[name=elegida_1]")
        p.fill("input[name=monto_1]", "20.000,00")
        p.locator("input[name=monto_1]").blur()
        icono = p.locator("[data-parcial]").first
        icono.hover()
        texto = icono.evaluate("e => getComputedStyle(e, '::after').content")
        self.assertEqual(texto, '"Cobro parcial"')
        self.assertEqual(
            icono.get_attribute("aria-label"),
            "Pago parcial: se cobran $20.000,00 de $50.000,00. Quedan $30.000,00.",
        )
        p.locator("[data-rama]").first.screenshot(path="e2e-capturas/024-parcial-1280.png")

    def test_efectivo_queda_finalizado_en_la_cuenta_de_efectivo(self):
        _, cobro = self.cobrar("efectivo")
        self.assertEqual(cobro.estado, Cobro.Estado.FINALIZADO)
        self.assertEqual(cobro.monto, D("10000.00"))
        self.assertEqual(cobro.cuenta, self.efectivo)
        self.assertEqual(cobro.items.get().monto, D("10000.00"))

    def test_pos_debito_finaliza_con_comprobante_y_cuenta_de_la_sesion(self):
        _, cuenta = self.terminal_con_sesion(acepta_debito=True)
        _, cobro = self.cobrar("debito", pos=POS)
        self.verificar_pos(cobro, "debito", cuenta)
        self.assertEqual(cobro.banco_emisor.nombre, "Banco Ejemplo")
        self.assertEqual(cobro.ultimos_4, "4321")

    def test_pos_credito_finaliza_con_comprobante_y_cuenta_de_la_sesion(self):
        _, cuenta = self.terminal_con_sesion(acepta_credito=True)
        _, cobro = self.cobrar("credito", pos=POS)
        self.verificar_pos(cobro, "credito", cuenta)
        self.assertEqual(cobro.ultimos_4, "4321")

    def test_qr_finaliza_con_comprobante_y_cuenta_de_la_sesion(self):
        _, cuenta = self.terminal_con_sesion(acepta_qr=True)
        _, cobro = self.cobrar("qr", pos=POS)
        self.verificar_pos(cobro, "qr", cuenta)
        self.assertIsNone(cobro.banco_emisor)

    def test_link_de_pago_finaliza_con_comprobante_y_cuenta_de_la_sesion(self):
        _, cuenta = self.terminal_con_sesion(acepta_link=True)
        _, cobro = self.cobrar("link", pos=POS)
        self.verificar_pos(cobro, "link", cuenta)
