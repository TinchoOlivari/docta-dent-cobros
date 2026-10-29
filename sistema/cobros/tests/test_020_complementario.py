"""020 Fase A: pago complementario (FR-011, FR-014)."""

import re
from decimal import Decimal as D

from django.urls import reverse

from sistema.cobros.tests.fabrica import topes_desde_enero
from sistema.cobros.tests.test_complementario_pantallas import BasePantalla
from sistema.destinatarios.servicios import (
    cambiar_estado_cuenta,
    crear_cuenta,
    crear_destinatario,
)

NUEVO = reverse("cobros:complementario_nuevo")


class FaltaDelMesTest(BasePantalla):
    def destino(self, tope, cobrado=None):
        d = crear_destinatario(usuario=None, nombre="Destinatario Ejemplo 2", tipo="profesional")
        c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=d,
            alias="ejemplo.dos",
            tope_mensual=D(tope),
        )
        topes_desde_enero()
        if cobrado:
            self.cobro(d, c, cobrado)
        return d

    def abrir(self, d, **kw):
        r = self.client.get(NUEVO, {"destinatario": d.pk, "mes": "2026-10", **kw})
        monto = re.search(r'<input[^>]*name="monto"[^>]*>', r.content.decode()).group(0)
        return r, monto

    def test_falta_positiva_llena_el_monto_y_lo_muestra(self):
        r, monto = self.abrir(self.destino("600000.01"))
        self.assertIn('value="600000,01"', monto)
        self.assertContains(r, "Falta del mes: $600.000,01")
        self.assertContains(r, "data-falta")

    def test_falta_de_un_centavo(self):
        r, monto = self.abrir(self.destino("1000.01", "1000.00"))
        self.assertIn('value="0,01"', monto)
        self.assertContains(r, "Falta del mes: $0,01")

    def test_falta_cero_deja_el_monto_vacio_y_muestra_cero(self):
        r, monto = self.abrir(self.destino("1000.00", "1000.00"))
        self.assertNotIn("value=", monto)
        self.assertContains(r, "Falta del mes: $0,00")

    def test_falta_negativa_deja_el_monto_vacio(self):
        r, monto = self.abrir(self.destino("1000.00", "1000.01"))
        self.assertNotIn("value=", monto)
        self.assertNotContains(r, "Falta del mes")

    def test_destinatario_invalido_no_muestra_el_texto(self):
        for valor in ("999999", "abc", ""):
            with self.subTest(valor=valor):
                r = self.client.get(NUEVO, {"destinatario": valor, "mes": "2026-10"})
                self.assertEqual(r.status_code, 200)
                self.assertNotContains(r, "Falta del mes")

    def test_sin_destinatario_no_muestra_el_texto(self):
        self.assertNotContains(self.client.get(NUEVO), "Falta del mes")


class NombreDeCuentaTest(BasePantalla):
    def test_selector_no_ofrece_la_cuenta_inactiva(self):
        d = crear_destinatario(usuario=None, nombre="Destinatario Ejemplo 1", tipo="profesional")
        c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=d,
            alias="ejemplo.uno",
            tope_mensual=D("1000.00"),
        )
        cambiar_estado_cuenta(c, "inactiva", usuario=None)
        r = self.client.get(NUEVO)
        self.assertNotContains(r, "ejemplo.uno")  # 021/FR-028: solo cuentas Activas
