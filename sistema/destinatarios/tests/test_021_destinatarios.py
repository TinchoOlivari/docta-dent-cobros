"""021 fase D: rótulos de cifras, "Sin tope", búsqueda sin acentos, consultas del detalle y emisor."""

import re
from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal


def fila_de(html, rotulo):
    """Texto de la celda `dd` que sigue al rótulo `dt`."""
    m = re.search(rf">{rotulo}</dt><dd[^>]*>([^<]*)</dd>", html)
    return m.group(1).strip() if m else None


class Base021D(SistemaTestCase):
    def setUp(self):
        p = mock.patch(
            "django.utils.timezone.now", return_value=datetime(2026, 10, 28, 12, 0, tzinfo=ARG)
        )
        p.start()
        self.addCleanup(p.stop)
        admin = get_user_model().objects.create_user(
            "adm021d", email="adm021d@example.com", role="administrator"
        )
        self.client.force_login(admin)
        self.d = crear_destinatario(usuario=None, nombre="Ejemplo Uno", tipo="profesional")

    def cuenta(self, alias="ejemplo.uno", destinatario=None, **topes):
        datos = {"tope_mensual": D("9000000.00"), "tope_semanal": D("0.00")} | topes
        return crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=destinatario or self.d,
            alias=alias, **datos,
        )  # fmt: skip


class RotulosTest(Base021D):
    def setUp(self):
        super().setUp()
        self.c = self.cuenta(alias="ejemplo.rotulos", tope_mensual=D("9000000.00"))
        cobro_prueba(
            cuenta=self.c,
            monto=D("630002.50"),
            estado="finalizado",
            fecha=datetime(2026, 10, 10, 12, tzinfo=ARG),
        )
        cobro_prueba(cuenta=self.c, monto=D("60000.04"), estado="pendiente", fecha=datetime(2026, 10, 11, 12, tzinfo=ARG))  # fmt: skip

    def test_lista_y_detalle_dicen_recibido_y_consumido(self):
        lista = self.client.get(reverse("destinatarios:lista")).content.decode()
        self.assertIn("$690.002,54", lista)
        self.assertIn("$630.002,50", lista)
        detalle = self.client.get(reverse("destinatarios:detalle", args=[self.d.pk]))
        html = detalle.content.decode()
        self.assertEqual(fila_de(html, "Consumido"), "$690.002,54")
        self.assertEqual(fila_de(html, "Recibido"), "$630.002,50")

    def test_prioridad_dice_recibido_y_reservado(self):
        html = self.client.get(reverse("asignacion:lista")).content.decode()
        self.assertIn("Recibido $630.002,50 · Reservado $60.000,04", html)


class BusquedaSinAcentosTest(Base021D):
    def test_nunez_encuentra_nunez_con_tilde(self):
        n = crear_destinatario(usuario=None, nombre="Ejemplo Núñez", tipo="profesional")
        for q in ("nunez", "NUNEZ", "Núñez", "núñez"):
            with self.subTest(q=q):
                r = self.client.get(reverse("destinatarios:lista"), {"q": q})
                self.assertEqual([d.pk for d in r.context["destinatarios"]], [n.pk])

    def test_alias_con_tilde_se_encuentra_sin_tilde(self):
        c = self.cuenta(alias="ejemplo.canon")
        c.alias = "ejemplo.cañón"
        c.save(update_fields=["alias"])
        r = self.client.get(reverse("destinatarios:lista"), {"q": "canon"})
        self.assertEqual([d.pk for d in r.context["destinatarios"]], [self.d.pk])

    def test_texto_sin_coincidencias_no_trae_nada(self):
        r = self.client.get(reverse("destinatarios:lista"), {"q": "zzz"})
        self.assertEqual(r.context["destinatarios"], [])


class ConsultasDelDetalleTest(Base021D):
    def consultas(self):
        url = reverse("destinatarios:detalle", args=[self.d.pk])
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx)

    def test_detalle_con_1_y_con_5_cuentas_hace_las_mismas_consultas(self):
        self.cuenta(alias="ejemplo.c1")
        con_una = self.consultas()
        for n in range(2, 6):
            c = self.cuenta(alias=f"ejemplo.c{n}", tope_semanal=D("100.00"))
            cobro_prueba(cuenta=c, monto=D("10.00"), estado="finalizado", fecha=datetime(2026, 10, 12, 12, tzinfo=ARG))  # fmt: skip
        self.assertEqual(self.consultas(), con_una)
