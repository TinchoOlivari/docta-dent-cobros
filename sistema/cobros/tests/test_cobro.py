from decimal import Decimal

from django.db import IntegrityError, transaction
from django.contrib import admin
from django.db.models import ProtectedError

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.auditoria.servicios import ENTIDADES
from sistema.cobros.models import Cobro
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario

D = Decimal


class CobroTest(SistemaTestCase):
    def setUp(self):
        dest = Destinatario.objects.create(nombre="Test", tipo="clinica", cuit="20000000001")
        self.cuenta = Cuenta.objects.create(
            destinatario=dest,
            tipo="transferencia",
            alias="alias.uno",
            tope_mensual=D("1000.00"),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )

    def test_monto_cero_falla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            cobro_prueba(cuenta=self.cuenta, monto=D("0.00"))

    def test_monto_un_centavo_se_guarda(self):
        self.assertIsNotNone(cobro_prueba(cuenta=self.cuenta, monto=D("0.01")).pk)

    def test_no_se_borra(self):
        cobro = cobro_prueba(cuenta=self.cuenta, monto=D("1.00"))
        with self.assertRaises(ProtectedError):
            cobro.delete()
        with self.assertRaises(ProtectedError):
            Cobro.objects.all().delete()

    def test_esta_en_entidades_de_auditoria(self):
        self.assertIn("cobros.cobro", ENTIDADES)

    def test_no_esta_en_el_admin(self):
        self.assertNotIn(Cobro, admin.site._registry)
