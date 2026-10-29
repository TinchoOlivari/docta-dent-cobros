import threading
from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.db import connection, transaction

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTransactionTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.servicios import (
    abrir_sesion,
    cambiar_cuenta,
    crear_terminal,
    cuenta_para_cobro,
)

AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=ARG)


def h(hora, minuto=0):
    return datetime(2026, 10, 14, hora, minuto, tzinfo=ARG)


class FilaSesionTest(SistemaTransactionTestCase):
    """FR-021, R5: 007 espera a una corrección de sesión en curso y ve su resultado."""

    def setUp(self):
        patcher = mock.patch("django.utils.timezone.now", return_value=AHORA)
        patcher.start()
        self.addCleanup(patcher.stop)
        mp = Proveedor.objects.get(nombre="Mercadopago")
        self.cuenta_a, self.cuenta_b = (
            crear_cuenta(
                usuario=None,
                tipo="terminal_pos",
                destinatario=Destinatario.objects.create(nombre=nombre, tipo="clinica"),
                titular="Titular Test",
                proveedor=mp,
                objetivo=Decimal("300000.00"),
            )
            for nombre in ("Rodrigo", "Jimena")
        )
        self.terminal = crear_terminal(
            usuario=None, nombre="Point Smart #1", proveedor=mp, acepta_debito=True
        )
        abrir_sesion(self.terminal, self.cuenta_a, h(9), usuario=None)
        cobro_prueba(
            cuenta=self.cuenta_a,
            terminal=self.terminal,
            monto=Decimal("1000.00"),
            estado="finalizado",
            metodo="debito",
            fecha=h(11, 30),
        )

    def test_cuenta_para_cobro_espera_la_correccion_y_devuelve_la_cuenta_corregida(self):
        dentro = threading.Event()  # hilo 1 tiene el bloqueo de la terminal
        soltar = threading.Event()  # el test deja que hilo 1 confirme
        resultado = {}

        def corrector():
            try:
                with transaction.atomic():
                    cambiar_cuenta(self.terminal, self.cuenta_b, h(11), usuario=None)
                    dentro.set()
                    if not soltar.wait(timeout=30):
                        raise TimeoutError
            except BaseException as e:  # noqa: BLE001
                resultado["h1"] = e
                dentro.set()
            finally:
                connection.close()

        def lector():
            try:
                with transaction.atomic():
                    resultado["cuenta"] = cuenta_para_cobro(self.terminal, h(11, 30))
            except BaseException as e:  # noqa: BLE001
                resultado["h2"] = e
            finally:
                connection.close()

        t1 = threading.Thread(target=corrector)
        t2 = threading.Thread(target=lector)
        t1.start()
        self.assertTrue(dentro.wait(timeout=30))
        t2.start()
        t2.join(timeout=1)
        # Si el bloqueo falla, hilo 2 ya habría terminado (con la cuenta vieja).
        self.assertTrue(t2.is_alive(), "hilo 2 no esperó a la corrección")
        soltar.set()
        t1.join(timeout=30)
        t2.join(timeout=30)
        self.assertEqual(resultado.get("h1"), None)
        self.assertEqual(resultado.get("h2"), None)
        self.assertEqual(resultado["cuenta"].pk, self.cuenta_b.pk)
