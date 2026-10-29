from sistema.core.tests import SistemaTestCase
from sistema.migracion.tests import fabricas as f


class FabricasTest(SistemaTestCase):
    def test_las_fabricas_arman_una_base_vieja_coherente(self):
        fila = f.fila("Destinatario Ejemplo", alias="alias.uno", priority_order=1)
        op = f.usuario("operador1")
        prof = f.usuario("drperez", role="professional")
        pago = f.pago(fila, 1000, op, prof, archivo="a.txt")
        regla = f.regla(fila)
        log = f.ejecucion(regla)
        self.assertEqual(fila.created_at, f.CREADO)
        self.assertEqual(log.executed_at, f.CREADO)
        self.assertEqual(pago.estado, "COMPLETADO")
        self.assertTrue(pago.proof_of_payment_file.name)
