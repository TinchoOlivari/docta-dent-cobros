from decimal import Decimal

from django.core.exceptions import ValidationError

from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Proveedor
from sistema.destinatarios.servicios import (
    crear_cuenta,
    crear_destinatario,
    crear_proveedor,
    editar_cuenta,
)

D = Decimal


class CuentaPosTest(SistemaTestCase):
    def setUp(self):
        self.d = crear_destinatario(usuario=None, nombre="Clínica Test", tipo="clinica")
        self.mp = Proveedor.objects.get(nombre="Mercadopago")

    def alta(self, **kw):
        datos = dict(titular="Ana Pérez", proveedor=self.mp, objetivo=D("300000.00"))
        datos.update(kw)
        return crear_cuenta(usuario=None, tipo="terminal_pos", destinatario=self.d, **datos)

    def rechaza(self, mensaje, **kw):
        with self.assertRaises(ValidationError) as cm:
            self.alta(**kw)
        self.assertIn(mensaje, cm.exception.messages)

    def test_us4_1_alta_queda_activa(self):
        c = self.alta()
        self.assertEqual(c.estado, "activa")
        self.assertEqual(c.proveedor, self.mp)
        self.assertEqual(c.objetivo, D("300000.00"))

    def test_us4_2_objetivo_cero_rechazado_un_centavo_ok(self):
        self.rechaza("El objetivo debe ser mayor que $0,00.", objetivo=D("0.00"))
        self.assertEqual(self.alta(objetivo=D("0.01")).objetivo, D("0.01"))

    def test_objetivo_negativo_rechazado(self):
        with self.assertRaises(ValidationError):
            self.alta(objetivo=D("-0.01"))

    def test_pos_sin_proveedor_rechazado(self):
        self.rechaza("Faltan datos de la cuenta de terminal POS.", proveedor=None)

    def test_pos_con_alias_rechazado(self):
        self.rechaza("Faltan datos de la cuenta de terminal POS.", alias="x.y")

    def test_us3_3_objetivo_se_edita(self):
        c = editar_cuenta(self.alta(), usuario=None, objetivo=D("250000.00"))
        c.refresh_from_db()
        self.assertEqual(c.objetivo, D("250000.00"))

    def test_edicion_con_objetivo_cero_rechazada(self):
        c = self.alta()
        with self.assertRaises(ValidationError):
            editar_cuenta(c, usuario=None, objetivo=D("0.00"))

    def test_proveedor_repetido_sin_mayusculas_rechazado(self):
        with self.assertRaises(ValidationError) as cm:
            crear_proveedor(usuario=None, nombre="MercadoPago")
        self.assertIn("Ya existe un proveedor con ese nombre.", cm.exception.messages)
