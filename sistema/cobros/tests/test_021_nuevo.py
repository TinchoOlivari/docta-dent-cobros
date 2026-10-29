"""021 Fase A: Nuevo cobro (FR-001 a FR-004, FR-006, FR-056)."""

from datetime import datetime
from unittest import mock

from django.urls import reverse

from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import con_elegidas
from sistema.cobros.tests.test_efectivo import (
    ARG,
    TRATAMIENTOS,
    BaseEfectivo,
)
from sistema.cobros.tests.test_permisos_cobros import BasePermisos
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento

NUEVO = f"{reverse('cobros:nuevo')}?paciente=501"
VACIO = "Elegí al menos una prestación."


class MontoContraSaldoTest(BaseEfectivo):
    """FR-002: saldo $12.000,50 de ejemplo; justo en el saldo, un centavo más, un centavo, cero."""

    def setUp(self):
        super().setUp()
        p = mock.patch(TRATAMIENTOS, return_value=[tratamiento([prestacion(1, "12000.50", "0")])])
        p.start()
        self.addCleanup(p.stop)
        self.client.force_login(self.ana)

    def enviar(self, monto):
        return self.client.post(NUEVO, con_elegidas({"metodo": "efectivo", "monto_1": monto}))

    def test_el_saldo_exacto_se_guarda(self):
        self.enviar("12.000,50")
        self.assertEqual(str(Cobro.objects.get().monto), "12000.50")

    def test_un_centavo_se_guarda(self):
        self.enviar("0,01")
        self.assertEqual(str(Cobro.objects.get().monto), "0.01")

    def test_un_centavo_sobre_el_saldo_da_el_error_y_no_guarda(self):
        r = self.enviar("12.000,51")
        self.assertContains(r, "supera el saldo disponible de $12.000,50")
        self.assertEqual(Cobro.objects.count(), 0)

    def test_cero_no_se_guarda(self):
        r = self.enviar("0,00")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Cobro.objects.count(), 0)

    def test_todo_vacio_dice_que_falta_un_monto(self):
        r = self.enviar("")
        self.assertContains(r, VACIO)
        self.assertEqual(Cobro.objects.count(), 0)

    def test_el_campo_lleva_el_saldo_en_centavos_para_el_navegador(self):
        self.assertContains(self.client.get(NUEVO), 'data-saldo="1200050"')


class FechaDelCobroTest(BasePermisos):
    """FR-056: fecha y hora en 2 campos."""

    def editar(self, cobro, **fecha):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("cobros:editar", args=[cobro.pk]),
            {
                "monto": "1000,00",
                "metodo": cobro.metodo,
                "cuenta": self.cuenta.pk,
                "estado_visto": cobro.estado,
                **{f"monto_item_{i.pk}": str(i.monto).replace(".", ",") for i in cobro.items.all()},
                **fecha,
            },
        )

    def test_fecha_25_10_2026_14_05_se_guarda(self):
        c = self.cobro(estado="finalizado")
        r = self.editar(c, fecha_0="25/10/2026", fecha_1="14:05")
        self.assertRedirects(r, reverse("cobros:detalle", args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.fecha, datetime(2026, 10, 25, 14, 5, tzinfo=ARG))

    def test_hora_invalida_da_el_error_y_no_cambia(self):
        c = self.cobro(estado="finalizado")
        antes = c.fecha
        r = self.editar(c, fecha_0="25/10/2026", fecha_1="25:00")
        self.assertContains(r, "Escribí la hora como HH:MM.")
        c.refresh_from_db()
        self.assertEqual(c.fecha, antes)
