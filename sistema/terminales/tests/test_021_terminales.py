from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.models import Sesion, Terminal

AHORA = datetime(2026, 10, 24, 14, 37, 52, tzinfo=ARG)


class Terminales021Test(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.prov = Proveedor.objects.get(nombre="Mercadopago")
        self.terminal = Terminal.objects.create(
            nombre="Terminal Ejemplo", proveedor=self.prov, acepta_qr=True
        )
        self.cuentas = [self._cuenta(i) for i in range(2)]
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.client.force_login(self.admin)

    def _cuenta(self, i):
        d = Destinatario.objects.create(nombre=f"Destinatario Ejemplo {i}", tipo="clinica")
        return crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=d,
            titular=f"Titular Ejemplo {i}",
            proveedor=self.prov,
            objetivo=Decimal("300000.00"),
        )

    def _sesion(self, cuenta, dia, fin=True):
        return Sesion.objects.create(
            terminal=self.terminal,
            cuenta=cuenta,
            inicio=datetime(2026, 10, dia, 8, 0, tzinfo=ARG),
            fin=datetime(2026, 10, dia, 9, 0, tzinfo=ARG) if fin else None,
        )

    def _post(self, nombre, pk, datos):
        with mock.patch("django.utils.timezone.now", return_value=AHORA):
            return self.client.post(reverse(nombre, args=[pk]), datos, follow=True)

    def test_cambiar_cuenta_guarda_la_hora_escrita_en_hora_argentina(self):
        s = self._sesion(self.cuentas[0], 20, fin=False)
        self._post(
            "terminales:cambiar_cuenta",
            self.terminal.pk,
            {"cuenta": self.cuentas[1].pk, "hora_0": "24/10/2026", "hora_1": "14:37"},
        )
        s.refresh_from_db()
        self.assertEqual(s.fin, datetime(2026, 10, 24, 14, 37, 0, tzinfo=ARG))

    def test_cerrar_guarda_la_hora_escrita_en_hora_argentina(self):
        s = self._sesion(self.cuentas[0], 20, fin=False)
        self._post("terminales:cerrar", s.pk, {"fin_0": "24/10/2026", "fin_1": "14:37"})
        s.refresh_from_db()
        self.assertEqual(s.fin, datetime(2026, 10, 24, 14, 37, 0, tzinfo=ARG))

    def test_hora_invalida_da_error_y_no_cierra(self):
        s = self._sesion(self.cuentas[0], 20, fin=False)
        r = self._post("terminales:cerrar", s.pk, {"fin_0": "24/10/2026", "fin_1": "25:00"})
        s.refresh_from_db()
        self.assertIsNone(s.fin)
        self.assertContains(r, "Escribí la hora como HH:MM.")

    def test_hora_23_59_es_valida_y_24_00_no(self):
        s = self._sesion(self.cuentas[0], 20, fin=False)
        self._post("terminales:cerrar", s.pk, {"fin_0": "24/10/2026", "fin_1": "24:00"})
        s.refresh_from_db()
        self.assertIsNone(s.fin)

    def test_cuentas_consultas_iguales_con_4_y_10_cuentas(self):
        url = reverse("terminales:cuentas")
        self.client.get(url)  # calienta cachés (contenttypes, sesión)
        for i in range(2, 4):
            self._cuenta(i)
        with self.assertNumQueries(self._contar(url)):
            self.client.get(url)
        for i in range(4, 10):
            self._cuenta(i)
        with self.assertNumQueries(self._contar(url, 4)):
            self.client.get(url)

    def _contar(self, url, esperadas=None):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as c:
            self.client.get(url)
        if esperadas is None:
            type(self)._base = len(c)
        return type(self)._base
