from datetime import date, datetime
from unittest import mock

from django.urls import reverse

from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG

from . import D, DatosMartinO

NOV_5 = datetime(2026, 11, 5, 10, 0, tzinfo=ARG)


class PantallaReporteTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse("reportes:destinatario", args=[self.martin.pk])

    def test_octubre_muestra_secciones_cuentas_y_totales(self):
        self.cargar_octubre()
        r = self.client.get(self.url, {"mes": "2026-10"})
        for texto in (
            "Transferencias",
            "— alias.uno",
            "$1.000.000,00",
            "$1.150.000,00",
            "$150.000,00",
        ):
            self.assertContains(r, texto)

    def test_sin_mes_usa_el_mes_corriente(self):
        with mock.patch("django.utils.timezone.now", return_value=NOV_5):
            self.client.force_login(self.admin)  # la sesión vence con el reloj fijo
            r = self.client.get(self.url)
        self.assertEqual(r.context["mes"], date(2026, 11, 1))

    def test_mes_futuro_vuelve_al_mes_corriente(self):
        with mock.patch("django.utils.timezone.now", return_value=NOV_5):
            self.client.force_login(self.admin)  # la sesión vence con el reloj fijo
            r = self.client.get(self.url, {"mes": "2027-01"})
        self.assertEqual(r.context["mes"], date(2026, 11, 1))

    def test_diferencia_negativa_muestra_signo(self):
        self.cobro(self.mp, "400000.00")  # supera el objetivo de 300000
        self.cobro(self.galicia, "500000.00")
        self.cobro(self.tincho, "100000.00")
        self.cobro(self.payway, "250000.00")
        r = self.client.get(self.url, {"mes": "2026-10"})
        self.assertEqual(r.context["reporte"].diferencia, D("-100000.00"))
        self.assertContains(r, "−$100.000,00")

    def test_consultas_constantes_con_4_y_con_8_cuentas(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def contar():
            with CaptureQueriesContext(connection) as c:
                self.client.get(self.url, {"mes": "2026-10"})
            return len(c)

        self.cargar_octubre()
        contar()  # calienta cachés (content types, sesión)
        con4 = contar()
        for i in range(2):
            self.cobro(self.transf(self.martin, f"extra{i}.alias", "1000.00"), "10.00")
            self.cobro(self.pos(self.martin, f"POS {i}", "Payway", "1000.00"), "10.00")
        self.assertEqual(contar(), con4)
