"""019 Fase B: cobrado hoy y del mes (FR-013) e Información de POS (FR-007 a FR-012)."""

import re
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.db import transaction
from django.urls import reverse

from payment_instructions.models import User
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.models import Terminal
from sistema.terminales.servicios import abrir_sesion, cerrar_sesion

D = Decimal
AHORA = datetime(2026, 10, 24, 12, 0, tzinfo=ARG)
PANEL = reverse("usuarios:panel")


class _Deshacer(Exception):
    pass


@contextmanager
def aislado():
    """Deshace lo creado en el caso: los registros no se borran (constitución I)."""
    try:
        with transaction.atomic():
            yield
            raise _Deshacer
    except _Deshacer:
        pass


def fecha(*args):
    return datetime(*args, tzinfo=ARG)


class Base(SistemaTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch("django.utils.timezone.now", return_value=AHORA)
        p.start()
        self.addCleanup(p.stop)
        self.admin = User.objects.create_user(
            "p19-admin", email="a@example.com", password="x", role="administrator"
        )
        self.client.force_login(self.admin)
        self.dest = Destinatario.objects.create(nombre="Destinatario Falso", tipo="clinica")
        self.prov = Proveedor.objects.get(nombre="Mercadopago")

    def html(self):
        return self.client.get(PANEL).content.decode()

    def cuenta_pos(self, objetivo="1000000.00"):
        return crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=self.dest,
            titular="Titular Falso",
            proveedor=self.prov,
            objetivo=D(objetivo),
        )

    def terminal(self, nombre):
        return Terminal.objects.create(nombre=nombre, proveedor=self.prov, acepta_debito=True)


class CobradoTest(Base):
    def cobro(self, monto, f, estado="finalizado", **kw):
        metodo = "transferencia" if estado == "pendiente" else "debito"
        return cobro_prueba(
            cuenta=self.cuenta_pos(), monto=D(monto), fecha=f, estado=estado, metodo=metodo, **kw
        )

    def card(self, r, cual):
        html = r.content.decode()
        i = html.index(f'data-cobrado="{cual}"')
        return re.sub(r"\s+", " ", html[i : html.index("</a>", i)])

    def test_limites_de_hoy_y_del_mes_en_hora_argentina(self):
        casos = [
            (fecha(2026, 10, 24, 0, 0, 0), True, True),
            (fecha(2026, 10, 23, 23, 59, 59), False, True),
            (fecha(2026, 10, 1, 0, 0, 0), False, True),
            (fecha(2026, 9, 30, 23, 59, 59), False, False),
            (fecha(2026, 10, 25, 0, 0, 0), False, True),  # mañana: no es hoy, sí es del mes
        ]
        for f, hoy, mes in casos:
            with self.subTest(f=f):
                with aislado():
                    self.cobro("10.00", f)
                    r = self.client.get(PANEL)
                    self.assertEqual(r.context["cobrado_hoy"], D("10.00") if hoy else D("0.00"))
                    self.assertEqual(r.context["cobrado_mes"], D("10.00") if mes else D("0.00"))

    def test_pendiente_y_cancelado_no_suman(self):
        self.cobro("30000.00", AHORA, estado="pendiente")
        self.cobro("40000.00", AHORA, estado="cancelado")
        r = self.client.get(PANEL)
        self.assertEqual(r.context["cobrado_hoy"], D("0.00"))
        self.assertEqual(r.context["cobrado_mes"], D("0.00"))

    def test_sin_cobros_dice_cero_en_las_dos_cards(self):
        r = self.client.get(PANEL)
        for cual in ("hoy", "mes"):
            self.assertIn("$0,00", self.card(r, cual))

    def test_un_centavo(self):
        self.cobro("0.01", AHORA)
        r = self.client.get(PANEL)
        self.assertIn("$0,01", self.card(r, "hoy"))

    def test_suma_con_centavos(self):
        self.cobro("100000.00", AHORA)
        self.cobro("50000.50", AHORA)
        r = self.client.get(PANEL)
        self.assertIn("$150.000,50", self.card(r, "hoy"))
        self.assertIn("$150.000,50", self.card(r, "mes"))

    def test_enlaces_exactos(self):
        r = self.client.get(PANEL)
        base = reverse("cobros:lista") + "?estado=finalizado"
        self.assertEqual(r.context["href_hoy"], f"{base}&desde=2026-10-24&hasta=2026-10-24")
        self.assertEqual(r.context["href_mes"], f"{base}&desde=2026-10-01&hasta=2026-10-31")

    def test_subtotal_de_la_lista_coincide_con_las_cards(self):
        self.cobro("100000.00", AHORA)
        self.cobro("50000.50", fecha(2026, 10, 3, 9, 0))
        self.cobro("7.00", fecha(2026, 9, 30, 23, 59, 59))
        r = self.client.get(PANEL)
        for cual, valor in (("hoy", r.context["cobrado_hoy"]), ("mes", r.context["cobrado_mes"])):
            lista = self.client.get(r.context[f"href_{cual}"])
            self.assertEqual(lista.context["subtotal"], valor)
        self.assertEqual(r.context["cobrado_mes"], D("150000.50"))


class PosTest(Base):
    def sesion(self, nombre, cobrado, objetivo="1000000.00", cuenta=None):
        cuenta = cuenta or self.cuenta_pos(objetivo)
        t = self.terminal(nombre)
        s = abrir_sesion(t, cuenta, fecha(2026, 10, 24, 9, 0), usuario=None)
        if D(cobrado):
            cobro_prueba(
                cuenta=cuenta,
                terminal=t,
                monto=D(cobrado),
                fecha=AHORA,
                metodo="debito",
                estado="finalizado",
            )
        return s

    def region(self):
        html = self.html()
        i = html.index('id="pos"') if 'id="pos"' in html else html.index("Información de POS")
        return re.sub(r"\s+", " ", html[i:])

    def test_orden_y_tonos_de_tres_terminales(self):
        self.sesion("T500", "500000.00")
        self.sesion("T850", "850000.00")
        self.sesion("T990", "990000.00")
        r = self.client.get(PANEL)
        self.assertEqual([s.terminal.nombre for s in r.context["pos"]], ["T990", "T850", "T500"])

    def test_empate_de_porcentaje_va_por_cobrado_y_luego_por_nombre(self):
        self.sesion("A", "900000.00", "1000000.00")
        self.sesion("B", "450000.00", "500000.00")
        self.sesion("C", "990000.00", "2000000.00")
        self.sesion("D", "450000.00", "500000.00")
        orden = [s.terminal.nombre for s in self.client.get(PANEL).context["pos"]]
        self.assertEqual(orden, ["A", "B", "D", "C"])

    def test_sesion_cerrada_no_aparece(self):
        s = self.sesion("T1", "0")
        cerrar_sesion(s, fecha(2026, 10, 24, 11, 0), usuario=None)
        r = self.client.get(PANEL)
        self.assertEqual(r.context["pos"], [])

    def test_una_cuenta_en_dos_terminales_son_dos_filas(self):
        cuenta = self.cuenta_pos()
        self.sesion("T1", "10.00", cuenta=cuenta)
        self.sesion("T2", "0", cuenta=cuenta)
        self.assertEqual(len(self.client.get(PANEL).context["pos"]), 2)

    def test_cancelado_no_suma(self):
        # Un cobro POS Pendiente no existe (constraint cobro_pendiente_solo_transferencia).
        s = self.sesion("T1", "250.00")
        cobro_prueba(
            cuenta=s.cuenta,
            terminal=s.terminal,
            monto=D("400.00"),
            fecha=AHORA,
            metodo="debito",
            estado="cancelado",
        )
        self.assertEqual(self.client.get(PANEL).context["pos"][0].cobrado, D("250.00"))

    def test_fila_muestra_cobrado_y_objetivo(self):
        self.sesion("T1", "850000.00")
        zona = re.sub(r"\s+", " ", self.html())
        self.assertIn("Cobrado $850.000,00 · Objetivo $1.000.000,00", zona)

    def test_consultas_crecen_una_por_sesion(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as base:
            self.client.get(PANEL)
        for n in range(5):
            self.sesion(f"T{n}", "10.00")
        with CaptureQueriesContext(connection) as con:
            self.client.get(PANEL)
        self.assertLessEqual(len(con) - len(base), 5)
