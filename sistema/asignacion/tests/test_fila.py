import threading
from collections import Counter
from datetime import datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Sum

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.asignacion.servicios import SinCuentaDisponible, crear_cobro_pendiente
from sistema.cobros.models import Cobro
from sistema.core.tests import SistemaTransactionTestCase
from sistema.destinatarios.models import Cuenta
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
ARG = ZoneInfo("America/Argentina/Buenos_Aires")
AHORA = datetime(2026, 10, 28, 12, 0, tzinfo=ARG)  # miércoles


class FilaTest(SistemaTransactionTestCase):
    """Cada hilo usa su propia conexión: por eso TransactionTestCase."""

    def setUp(self):
        patcher = mock.patch("django.utils.timezone.now", return_value=AHORA)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = get_user_model().objects.create_user(
            "fila-admin", email="fila@example.com", role="administrator"
        )
        dm = crear_destinatario(usuario=None, nombre="Destinatario Ejemplo", tipo="profesional")
        dj = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        dr = crear_destinatario(usuario=None, nombre="Rodrigo", tipo="profesional")
        self.c1 = self.transf(dm, "alias.uno", "500000.00", "0.00", "10000.00")
        self.c2 = self.transf(dj, "jimena.roble.rana", "300000.00", "100000.00", "0.00")
        self.c3 = self.transf(dr, "rodrigo.mp", "200000.00", "0.00", "50000.00")

    def transf(self, dest, alias, mensual, semanal, minimo):
        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias=alias,
            tope_mensual=D(mensual),
            tope_semanal=D(semanal),
            minimo_por_pago=D(minimo),
        )

    def cobro(self, cuenta, monto):
        return cobro_prueba(cuenta=cuenta, monto=D(monto), estado="finalizado", fecha=AHORA)

    def pedir_en_paralelo(self, montos):
        """Un hilo por monto; la barrera los suelta juntos. Devuelve Cobro o SinCuentaDisponible."""
        barrera = threading.Barrier(len(montos), timeout=30)
        resultados = [None] * len(montos)

        def trabajo(i, monto):
            try:
                barrera.wait()
                resultados[i] = crear_cobro_pendiente(monto=D(monto), usuario=self.user)
            except SinCuentaDisponible as e:
                resultados[i] = e
            except BaseException as e:  # noqa: BLE001
                resultados[i] = e
            finally:
                connection.close()

        hilos = [threading.Thread(target=trabajo, args=(i, m)) for i, m in enumerate(montos)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=60)
        for r in resultados:
            if isinstance(r, BaseException) and not isinstance(r, SinCuentaDisponible):
                self.fail(f"error inesperado en un hilo: {r!r}")
            self.assertIsNotNone(r, "un hilo no terminó")
        return resultados

    def aliases(self, resultados):
        return Counter(r.cuenta.alias if isinstance(r, Cobro) else "sin-cuenta" for r in resultados)

    def test_us3_1_dos_pedidos_no_pasan_el_cupo_de_la_primera(self):
        self.cobro(self.c1, "450000.00")
        res = self.pedir_en_paralelo(["50000.00", "50000.00"])
        self.assertEqual(self.aliases(res), Counter({"alias.uno": 1, "jimena.roble.rana": 1}))

    def test_us3_2_con_cupo_para_uno_el_otro_no_tiene_cuenta(self):
        self.cobro(self.c1, "450000.00")
        self.cobro(self.c2, "300000.00")
        self.cobro(self.c3, "200000.00")
        res = self.pedir_en_paralelo(["50000.00", "50000.00"])
        self.assertEqual(self.aliases(res), Counter({"alias.uno": 1, "sin-cuenta": 1}))

    def test_sc002_veinte_pedidos_respetan_los_topes(self):
        res = self.pedir_en_paralelo(["50000.00"] * 20)
        self.assertEqual(
            self.aliases(res),
            Counter({"alias.uno": 10, "jimena.roble.rana": 2, "rodrigo.mp": 4, "sin-cuenta": 4}),
        )
        for cuenta in Cuenta.objects.all():
            total = cuenta.cobros.filter(estado="pendiente").aggregate(s=Sum("monto"))["s"]
            self.assertLessEqual(total, cuenta.tope_mensual)
            if cuenta.tope_semanal:
                self.assertLessEqual(total, cuenta.tope_semanal)
        self.assertFalse(Cobro.objects.filter(excedente=True).exists())
