from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.urls import reverse

from payment_instructions.models import User
from sistema.arca.tests.fabricas import cobro_facturable, factura_prueba
from sistema.cobros.models import ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
AHORA = datetime(2026, 10, 28, 12, 0, tzinfo=ARG)
PANEL = reverse("usuarios:panel")
ENVIOS = reverse("dentalink:envios") + "?estado=error"
EXCEDENTES = reverse("cobros:lista") + "?excedente_sin_revisar=on"
FACTURAS = reverse("cobros:lista") + "?factura_con_error=on"


class PanelTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("django.utils.timezone.now", return_value=AHORA)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = User.objects.create_user(
            "panel-admin", email="a@example.com", password="x", role="administrator"
        )
        self.client.force_login(self.admin)

    def cuenta(self, tope="1000000.00", alias="cuenta.panel"):
        dest = crear_destinatario(usuario=None, nombre="Destinatario Prueba", tipo="profesional")
        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias=alias,
            tope_mensual=D(tope),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )

    def contador(self, html, href):
        """Texto del número dentro del enlace que apunta a `href`."""
        inicio = html.index(f'href="{href}"')
        return html[inicio : html.index("</a>", inicio)]

    def test_contadores_cuentan_error_excedente_sin_revisar_y_factura_con_error(self):
        cuenta = self.cuenta()
        cobro = cobro_prueba(cuenta=cuenta, monto=D("300.00"), fecha=AHORA)
        for n, estado in enumerate(("error", "error", "error", "enviado", "pendiente"), start=1):
            ItemCobro.objects.create(cobro=cobro, numero=n, monto=D("1.00"), estado_envio=estado)
        for revisado in (False, False, True):
            cobro_prueba(
                cuenta=cuenta, monto=D("10.00"), fecha=AHORA, excedente=True, revisado=revisado
            )
        con_error = cobro_facturable()
        factura_prueba(con_error, estado="error", motivo="Rechazo de prueba")
        factura_prueba(cobro_facturable(), estado="pendiente")
        html = self.client.get(PANEL).content.decode()
        for href, n in ((ENVIOS, 3), (EXCEDENTES, 2), (FACTURAS, 1)):
            with self.subTest(href=href):
                tarjeta = self.contador(html, href)
                self.assertIn(f">{n}<", tarjeta.replace(" ", "").replace("\n", ""))

    def test_un_solo_item_en_error_ya_es_alerta_y_dos_cobros_con_factura_error_cuentan_dos(self):
        cuenta = self.cuenta()
        cobro = cobro_prueba(cuenta=cuenta, monto=D("1.00"), fecha=AHORA)
        ItemCobro.objects.create(cobro=cobro, numero=1, monto=D("1.00"), estado_envio="error")
        for _ in range(2):
            factura_prueba(cobro_facturable(), estado="error", motivo="Rechazo de prueba")
        r = self.client.get(PANEL)
        self.assertEqual(r.context["pendientes"][0][1], 1)
        self.assertEqual(r.context["pendientes"][2][1], 2)

    def test_todo_en_cero_muestra_tres_contadores_cero_sin_alerta(self):
        r = self.client.get(PANEL)
        self.assertEqual([n for _, n, _ in r.context["pendientes"]], [0, 0, 0, 0])
        self.assertEqual(
            [h for _, _, h in r.context["pendientes"]][:3], [ENVIOS, EXCEDENTES, FACTURAS]
        )

    def test_operador_y_profesional_reciben_403_y_anonimo_va_al_login(self):
        for rol in ("operator", "professional"):
            with self.subTest(rol=rol):
                self.client.force_login(
                    User.objects.create_user(
                        f"p-{rol}", email=f"{rol}@example.com", password="x", role=rol
                    )
                )
                self.assertEqual(self.client.get(PANEL).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(PANEL).status_code, 302)
