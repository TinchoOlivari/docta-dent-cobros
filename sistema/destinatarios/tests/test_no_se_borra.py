from django.db.models import ProtectedError

from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario, Proveedor


class NoSeBorraTest(SistemaTestCase):
    def setUp(self):
        self.d = Destinatario.objects.create(nombre="Test", tipo="clinica")
        self.c = Cuenta.objects.create(destinatario=self.d, tipo="efectivo")

    def test_delete_lanza_error_y_no_baja_la_cantidad(self):
        for obj in (self.d, self.c, Proveedor.objects.first()):
            with self.assertRaises(ProtectedError):
                obj.delete()
        with self.assertRaises(ProtectedError):
            Destinatario.objects.all().delete()
        self.assertEqual(Destinatario.objects.count(), 1)
        self.assertEqual(Cuenta.objects.count(), 1)
        self.assertEqual(Proveedor.objects.count(), 3)

    def test_existen_los_tres_proveedores(self):
        nombres = set(Proveedor.objects.values_list("nombre", flat=True))
        self.assertEqual(nombres, {"Mercadopago", "Takataka", "Payway"})
