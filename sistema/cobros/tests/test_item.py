from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.auditoria.servicios import ENTIDADES
from sistema.cobros.models import ItemCobro
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario

D = Decimal


class ItemCobroTest(SistemaTestCase):
    def setUp(self):
        dest = Destinatario.objects.create(nombre="Test", tipo="clinica", cuit="20000000001")
        cuenta = Cuenta.objects.create(
            destinatario=dest,
            tipo="transferencia",
            alias="alias.uno",
            tope_mensual=D("1000.00"),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )
        self.cobro = cobro_prueba(cuenta=cuenta, monto=D("100.00"))

    def item(self, numero=1, **kw):
        kw.setdefault("monto", D("1.00"))
        return ItemCobro.objects.create(cobro=self.cobro, numero=numero, **kw)

    def test_monto_cero_falla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.item(monto=D("0.00"))

    def test_monto_un_centavo_se_guarda_pendiente(self):
        self.assertEqual(self.item(monto=D("0.01")).estado_envio, "pendiente")

    def test_numero_repetido_en_el_cobro_falla(self):
        self.item(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.item(1)

    def test_intentos_seis_se_guarda_y_siete_falla(self):
        self.item(1, intentos=6)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.item(2, intentos=7)

    def test_referencia_es_cobro_y_numero(self):
        self.assertEqual(self.item(2).referencia, f"{self.cobro.pk}-2")

    def test_no_se_borra(self):
        item = self.item()
        with self.assertRaises(ProtectedError):
            item.delete()
        with self.assertRaises(ProtectedError):
            ItemCobro.objects.all().delete()

    def test_esta_en_entidades_de_auditoria(self):
        self.assertIn("cobros.itemcobro", ENTIDADES)

    def test_cobro_nuevo_tiene_defaults(self):
        self.assertEqual(self.cobro.metodo, "transferencia")
        self.assertFalse(self.cobro.faltan_items)
