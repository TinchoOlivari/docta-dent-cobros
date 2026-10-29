from decimal import Decimal

from django.db import IntegrityError, transaction

from sistema.cobros.models import Cobro
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario

D = Decimal


class CobroMigradoTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        dest = Destinatario.objects.create(nombre="Test", tipo="clinica", cuit="20000000001")
        self.cuenta = Cuenta.objects.create(
            destinatario=dest,
            tipo="transferencia",
            alias="alias.uno",
            tope_mensual=D("100000.00"),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )

    def crear(self, **kw):
        return Cobro.objects.create(
            cuenta=self.cuenta,
            monto=D("100.00"),
            metodo="transferencia",
            estado="finalizado",
            comprobante="",
            **kw,
        )

    def test_finalizado_sin_comprobante_y_no_migrado_falla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.crear(migrado=False)

    def test_finalizado_sin_comprobante_y_migrado_se_guarda(self):
        self.assertTrue(self.crear(migrado=True).migrado)

    def test_migrado_es_falso_por_defecto(self):
        self.assertFalse(Cobro._meta.get_field("migrado").default)

    def test_no_migrado_con_comprobante_se_guarda(self):
        cobro = Cobro.objects.create(
            cuenta=self.cuenta,
            monto=D("100.00"),
            metodo="transferencia",
            estado="finalizado",
            comprobante="comprobantes/a.pdf",
        )
        self.assertFalse(cobro.migrado)
