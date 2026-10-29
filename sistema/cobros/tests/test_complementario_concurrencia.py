import threading
from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection

from sistema.asignacion.servicios import SinCuentaDisponible, crear_cobro_pendiente
from sistema.cobros import servicios
from sistema.cobros.models import Cobro, PagoComplementario
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTransactionTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
AHORA = datetime(2026, 10, 20, 10, 0, tzinfo=ARG)


class PagoContraCobroTest(SistemaTransactionTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        User = get_user_model()
        self.admin = User.objects.create_user("admin", email="a@example.com", role="administrator")
        self.ana = User.objects.create_user("ana", email="ana@example.com", role="operator")
        self.dest = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="jimena.roble.rana",
            tope_mensual=D("100000.00"),
        )
        cobro_prueba(
            destinatario=self.dest,
            cuenta=self.cuenta,
            monto=D("80000.00"),
            estado="finalizado",
            fecha=AHORA,
        )

    def test_el_cobro_espera_el_pago_y_no_encuentra_cupo(self):
        dentro, soltar = threading.Event(), threading.Event()
        original = servicios._con_archivo
        resultado = {}

        def lento(pago, archivo):
            original(pago, archivo)
            dentro.set()  # ya tiene la fila
            soltar.wait(timeout=30)

        def pagar():
            try:
                with mock.patch.object(servicios, "_con_archivo", lento):
                    servicios.registrar_pago_complementario(
                        usuario=self.admin,
                        destinatario=self.dest,
                        mes=date(2026, 10, 1),
                        monto=D("20000.00"),
                        metodo="transferencia",
                        cuenta=self.cuenta,
                    )
            finally:
                connection.close()

        def cobrar():
            try:
                crear_cobro_pendiente(monto=D("10000.00"), usuario=self.ana)
            except SinCuentaDisponible as e:
                resultado["error"] = e
            finally:
                connection.close()

        h1 = threading.Thread(target=pagar)
        h1.start()
        self.assertTrue(dentro.wait(timeout=30))
        h2 = threading.Thread(target=cobrar)
        h2.start()
        h2.join(timeout=0.5)
        self.assertTrue(h2.is_alive())  # espera la fila del pago
        soltar.set()
        h1.join(timeout=30)
        h2.join(timeout=30)
        self.assertEqual(PagoComplementario.objects.count(), 1)
        self.assertIn("error", resultado)
        self.assertEqual(Cobro.objects.filter(estado="pendiente").count(), 0)
