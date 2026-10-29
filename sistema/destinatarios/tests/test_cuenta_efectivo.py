from decimal import Decimal

from django.core.exceptions import ValidationError

from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta
from sistema.destinatarios.servicios import (
    cambiar_estado_cuenta,
    crear_cuenta,
    crear_destinatario,
)

MSG = "Ya hay una cuenta de efectivo activa."


class CuentaEfectivoTest(SistemaTestCase):
    def setUp(self):
        self.d = crear_destinatario(usuario=None, nombre="Clínica", tipo="clinica")

    def alta(self, **kw):
        return crear_cuenta(usuario=None, tipo="efectivo", destinatario=self.d, **kw)

    def test_us5_1_sin_montos(self):
        c = self.alta()
        self.assertEqual(c.estado, "activa")
        for campo in ("tope_mensual", "tope_semanal", "minimo_por_pago", "objetivo"):
            self.assertIsNone(getattr(c, campo))

    def test_efectivo_con_monto_rechazado_incluso_cero(self):
        for campo in ("tope_mensual", "tope_semanal", "minimo_por_pago", "objetivo"):
            for valor in (Decimal("0.00"), Decimal("0.01")):
                with self.assertRaises(ValidationError, msg=f"{campo} {valor}"):
                    self.alta(**{campo: valor})

    def test_us5_2_segunda_activa_rechazada(self):
        self.alta()
        with self.assertRaises(ValidationError) as cm:
            self.alta()
        self.assertIn(MSG, cm.exception.messages)

    def test_us5_2_segunda_inactiva_ok_y_no_se_activa_con_otra_activa(self):
        self.alta()
        b = self.alta(estado="inactiva")
        with self.assertRaises(ValidationError) as cm:
            cambiar_estado_cuenta(b, "activa", usuario=None)
        self.assertIn(MSG, cm.exception.messages)

    def test_us5_2_archivar_la_primera_permite_activar_la_segunda(self):
        a = self.alta()
        b = self.alta(estado="inactiva")
        cambiar_estado_cuenta(a, "archivada", usuario=None)
        cambiar_estado_cuenta(b, "activa", usuario=None)
        self.assertEqual(Cuenta.objects.get(pk=b.pk).estado, "activa")
