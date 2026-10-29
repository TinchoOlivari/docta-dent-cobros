from datetime import datetime
from decimal import Decimal
from unittest import mock

from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.forms import CambiarCuentaForm, CerrarSesionForm
from sistema.terminales.models import Terminal


def _ahora(h, m, s, dia=24):
    return datetime(2026, 10, dia, h, m, s, tzinfo=ARG)


class HoraInicialTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        prov = Proveedor.objects.get(nombre="Mercadopago")
        d = Destinatario.objects.create(nombre="Destinatario Ejemplo", tipo="clinica")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=d,
            titular="Titular Ejemplo",
            proveedor=prov,
            objetivo=Decimal("300000.00"),
        )
        self.terminal = Terminal.objects.create(
            nombre="Terminal Ejemplo", proveedor=prov, acepta_qr=True
        )

    def _valores(self):
        c = CambiarCuentaForm(terminal=self.terminal)["hora"].value()
        f = CerrarSesionForm()["fin"].value()
        return c, f

    def test_hora_y_fin_vienen_con_la_hora_argentina_al_minuto(self):
        with mock.patch("django.utils.timezone.now", return_value=_ahora(14, 37, 52)):
            for valor in self._valores():
                self.assertEqual(valor.replace(tzinfo=None), datetime(2026, 10, 24, 14, 37))
            html = str(CerrarSesionForm()["fin"])
        self.assertIn('name="fin_0" value="2026-10-24"', html)
        self.assertIn('name="fin_1" value="14:37"', html)

    def test_a_las_23_59_40_el_valor_es_23_59_y_a_las_00_00_10_se_acepta(self):
        with mock.patch("django.utils.timezone.now", return_value=_ahora(23, 59, 40)):
            self.assertEqual(
                self._valores()[1].replace(tzinfo=None), datetime(2026, 10, 24, 23, 59)
            )
        datos = {"cuenta": self.cuenta.pk, "hora_0": "24/10/2026", "hora_1": "23:59"}
        with mock.patch("django.utils.timezone.now", return_value=_ahora(0, 0, 10, dia=25)):
            form = CambiarCuentaForm(datos, terminal=self.terminal)
            self.assertTrue(form.is_valid(), form.errors)
