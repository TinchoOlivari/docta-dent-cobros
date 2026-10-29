from datetime import datetime
from decimal import Decimal

from django.urls import reverse

from payment_instructions.models import User
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta, Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.models import Sesion, Terminal
from sistema.terminales.servicios import crear_terminal
from sistema.terminales.tests.test_correccion import CorreccionBase
from sistema.terminales.tests.test_sesion import SesionBase, h

AHORA = datetime(2026, 10, 14, 10, 0, tzinfo=ARG)


class _Base(SistemaTestCase):
    def setUp(self):
        self.mp = Proveedor.objects.get(nombre="Mercadopago")
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.operador = User.objects.create_user(
            "op", email="op@example.com", password="x", role="operator"
        )
        self.profesional = User.objects.create_user(
            "pro", email="pro@example.com", password="x", role="professional"
        )
        self.t = crear_terminal(
            usuario=None, nombre="Point 1", proveedor=self.mp, acepta_debito=True
        )


class TerminalPantallasTest(_Base):
    def test_admin_crea_por_post_y_la_ve_en_la_lista(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("terminales:nueva"),
            {"nombre": "Point 2", "proveedor": self.mp.pk, "acepta_qr": "on"},
        )
        t = Terminal.objects.get(nombre="Point 2")
        self.assertRedirects(r, reverse("terminales:detalle", args=[t.pk]))
        self.assertTrue(t.acepta_qr)
        self.assertContains(self.client.get(reverse("terminales:lista")), "Point 2")

    def test_post_sin_metodos_muestra_el_mensaje(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("terminales:nueva"), {"nombre": "Point 3", "proveedor": self.mp.pk}
        )
        self.assertContains(r, "Elegí al menos un método de cobro.")
        self.assertFalse(Terminal.objects.filter(nombre="Point 3").exists())

    def test_editar_no_tiene_campo_proveedor(self):
        self.client.force_login(self.admin)
        url = reverse("terminales:editar", args=[self.t.pk])
        self.assertNotContains(self.client.get(url), 'name="proveedor"')
        r = self.client.post(url, {"nombre": "Point Uno", "acepta_debito": "on"})
        self.assertRedirects(r, reverse("terminales:detalle", args=[self.t.pk]))
        self.t.refresh_from_db()
        self.assertEqual(self.t.nombre, "Point Uno")

    def test_estado_con_sesion_abierta_muestra_el_mensaje(self):
        d = Destinatario.objects.create(nombre="Rodrigo", tipo="clinica")
        cuenta = crear_cuenta(
            usuario=None, tipo="terminal_pos", destinatario=d, titular="Titular Test",
            proveedor=self.mp, objetivo=Decimal("300000.00"),
        )  # fmt: skip
        Sesion.objects.create(terminal=self.t, cuenta=cuenta, inicio=AHORA)
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("terminales:estado", args=[self.t.pk]), {"estado": "inactiva"}, follow=True
        )
        self.assertContains(r, "La terminal tiene una sesión abierta. Cerrá la sesión primero.")
        self.t.refresh_from_db()
        self.assertEqual(self.t.estado, "activa")
        self.assertContains(self.client.get(reverse("terminales:lista")), "Titular Test")


class PermisosTest(_Base):
    def setUp(self):
        super().setUp()
        d = Destinatario.objects.create(nombre="Rodrigo", tipo="clinica")
        cuenta = crear_cuenta(
            usuario=None, tipo="terminal_pos", destinatario=d, titular="Titular Test",
            proveedor=self.mp, objetivo=Decimal("300000.00"),
        )  # fmt: skip
        self.sesion = Sesion.objects.create(terminal=self.t, cuenta=cuenta, inicio=AHORA)

    def urls(self):
        pk = self.t.pk
        return [
            reverse("terminales:lista"),
            reverse("terminales:nueva"),
            reverse("terminales:detalle", args=[pk]),
            reverse("terminales:editar", args=[pk]),
            reverse("terminales:estado", args=[pk]),
            reverse("terminales:abrir", args=[pk]),
            reverse("terminales:cambiar_cuenta", args=[pk]),
            reverse("terminales:cuentas"),
            reverse("terminales:acumula_desde", args=[self.sesion.cuenta_id]),
        ] + [
            reverse(f"terminales:{n}", args=[self.sesion.pk])
            for n in ("cerrar", "editar_sesion", "corregir", "borrar_sesion")
        ]

    def test_sin_sesion_redirige_al_login(self):
        for url in self.urls():
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login/", r["Location"])

    def test_operador_y_profesional_reciben_403_en_get_y_post(self):
        for usuario in (self.operador, self.profesional):
            self.client.force_login(usuario)
            for url in self.urls():
                self.assertEqual(self.client.get(url).status_code, 403, url)
                self.assertEqual(self.client.post(url, {}).status_code, 403, url)
        self.sesion.refresh_from_db()
        self.assertIsNone(self.sesion.fin)
        self.assertEqual(Sesion.objects.count(), 1)


class _AdminMixin:
    def login_admin(self):
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.client.force_login(self.admin)

    def detalle(self):
        return reverse("terminales:detalle", args=[self.terminal.pk])


class SesionPantallasTest(_AdminMixin, SesionBase):
    def setUp(self):
        super().setUp()
        self.login_admin()

    def test_abrir_por_post_y_se_ve_en_el_detalle(self):
        r = self.client.post(
            reverse("terminales:abrir", args=[self.terminal.pk]),
            {"cuenta": self.cuenta_a.pk, "inicio_0": "14/10/2026", "inicio_1": "09:00"},
            follow=True,
        )
        self.assertContains(r, "Sesión abierta.")
        self.assertContains(r, "14/10/2026 09:00:00")
        s = Sesion.objects.get()
        self.assertEqual(s.inicio, h(9))

    def test_cambiar_cuenta_y_cerrar(self):
        self.abrir()
        r = self.client.post(
            reverse("terminales:cambiar_cuenta", args=[self.terminal.pk]),
            {"cuenta": self.cuenta_b.pk, "hora_0": "14/10/2026", "hora_1": "09:30"},
            follow=True,
        )
        self.assertContains(r, "Cuenta cambiada.")
        nueva = Sesion.objects.get(fin__isnull=True)
        self.assertEqual(nueva.cuenta, self.cuenta_b)
        r = self.client.post(
            reverse("terminales:cerrar", args=[nueva.pk]),
            {"fin_0": "14/10/2026", "fin_1": "09:45"},
            follow=True,
        )
        self.assertContains(r, "Sesión cerrada.")
        nueva.refresh_from_db()
        self.assertEqual(nueva.fin, h(9, 45))

    def test_superposicion_se_muestra_como_mensaje(self):
        self.abrir(inicio=h(9))
        r = self.client.post(
            reverse("terminales:abrir", args=[self.terminal.pk]),
            {"cuenta": self.cuenta_b.pk, "inicio_0": "14/10/2026", "inicio_1": "09:30"},
            follow=True,
        )
        self.assertContains(r, "La sesión se superpone con otra sesión de la terminal.")
        self.assertEqual(Sesion.objects.count(), 1)

    def test_el_form_de_cuenta_no_lista_cuentas_de_otro_proveedor(self):
        otra = self.pos(self.rodrigo, proveedor=self.payway)
        r = self.client.get(self.detalle())
        self.assertContains(r, f'value="{self.cuenta_a.pk}"')
        self.assertNotContains(r, f'value="{otra.pk}"')
        r = self.client.post(
            reverse("terminales:abrir", args=[self.terminal.pk]),
            {"cuenta": otra.pk, "inicio_0": "14/10/2026", "inicio_1": "09:00"},
            follow=True,
        )
        self.assertFalse(Sesion.objects.exists())
        self.assertContains(r, "Seleccione una opción válida", status_code=200)


class CorregirPantallaTest(_AdminMixin, CorreccionBase):
    def setUp(self):
        super().setUp()
        self.login_admin()

    def test_post_corrige_y_muestra_la_cantidad(self):
        self.cobro(h(11, 30))
        r = self.client.post(
            reverse("terminales:corregir", args=[self.a.pk]),
            {"hora_0": "14/10/2026", "hora_1": "11:00"},
            follow=True,
        )
        self.assertContains(r, "Cambio corregido. Cobros reasignados: 1.")

    def test_borrar_sesion_sin_cobros(self):
        r = self.client.post(reverse("terminales:borrar_sesion", args=[self.a.pk]), follow=True)
        self.assertContains(r, "Sesión borrada.")
        self.assertFalse(Sesion.objects.filter(pk=self.a.pk).exists())

    def test_borrar_sesion_con_cobros_muestra_el_error(self):
        self.cobro(h(10))
        r = self.client.post(reverse("terminales:borrar_sesion", args=[self.a.pk]), follow=True)
        self.assertContains(r, "La sesión tiene cobros. No se puede borrar.")
        self.assertTrue(Sesion.objects.filter(pk=self.a.pk).exists())


class CuentasPantallaTest(_AdminMixin, SesionBase):
    def setUp(self):
        super().setUp()
        self.login_admin()
        Cuenta.objects.update(acumula_desde=h(0))
        cobro_prueba(
            cuenta=self.cuenta_a, terminal=self.terminal, destinatario=self.rodrigo,
            monto=Decimal("250000.00"), estado="finalizado", metodo="debito", fecha=h(9),
        )  # fmt: skip

    def test_muestra_objetivo_y_acumulado(self):
        r = self.client.get(reverse("terminales:cuentas"))
        self.assertContains(r, "$300.000,00")
        self.assertContains(r, "$250.000,00")
        self.assertContains(r, "14/10/2026 00:00:00")

    def test_no_lista_cuentas_de_transferencia(self):
        crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=self.rodrigo,
            titular="Titular Transfer", alias="alias.test.uno", tope_mensual=Decimal("1000.00"),
        )  # fmt: skip
        r = self.client.get(reverse("terminales:cuentas"))
        self.assertNotContains(r, "Titular Transfer")

    def test_lista_cuenta_archivada_con_su_estado(self):
        Cuenta.objects.filter(pk=self.cuenta_b.pk).update(estado="archivada")
        self.assertContains(self.client.get(reverse("terminales:cuentas")), "Archivada")

    def test_post_reiniciar_cambia_acumula_desde(self):
        r = self.client.post(
            reverse("terminales:acumula_desde", args=[self.cuenta_a.pk]),
            {"fecha_0": "14/10/2026", "fecha_1": "10:00"},
            follow=True,
        )
        self.assertContains(r, "Acumulado reiniciado.")
        self.cuenta_a.refresh_from_db()
        self.assertEqual(self.cuenta_a.acumula_desde, h(10))
        self.assertContains(r, "$0,00")

    def test_post_en_el_futuro_muestra_el_error(self):
        r = self.client.post(
            reverse("terminales:acumula_desde", args=[self.cuenta_a.pk]),
            {"fecha_0": "14/10/2026", "fecha_1": "10:01"},
            follow=True,
        )
        self.assertContains(r, "no puede estar en el futuro")
        self.cuenta_a.refresh_from_db()
        self.assertEqual(self.cuenta_a.acumula_desde, h(0))
