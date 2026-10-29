from decimal import Decimal

from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario


class PantallasTest(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.operador = User.objects.create_user(
            "op", email="op@example.com", password="x", role="operator"
        )
        self.d = crear_destinatario(usuario=None, nombre="Ana Test", tipo="profesional")

    def urls(self):
        return [
            reverse("destinatarios:lista"),
            reverse("destinatarios:nuevo"),
            reverse("destinatarios:detalle", args=[self.d.pk]),
            reverse("destinatarios:editar", args=[self.d.pk]),
            reverse("destinatarios:nueva_cuenta", args=[self.d.pk, "transferencia"]),
        ]

    def test_operador_recibe_403_en_cada_url(self):
        self.client.force_login(self.operador)
        for url in self.urls():
            self.assertEqual(self.client.get(url).status_code, 403, url)
            self.assertEqual(self.client.post(url, {}).status_code, 403, url)

    def test_sin_login_redirige(self):
        for url in self.urls():
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login/", r["Location"])

    def test_administrador_crea_destinatario_y_cuenta_por_post(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("destinatarios:nuevo"),
            {"nombre": "Beto Test", "tipo": "proveedor", "cuit": ""},
        )
        beto = Destinatario.objects.get(nombre="Beto Test")
        self.assertRedirects(r, reverse("destinatarios:detalle", args=[beto.pk]))
        url = reverse("destinatarios:nueva_cuenta", args=[beto.pk, "transferencia"])
        r = self.client.post(url, self.datos_cuenta(beto, minimo_por_pago="10000,00"))
        self.assertRedirects(r, reverse("destinatarios:detalle", args=[beto.pk]))
        cuenta = Cuenta.objects.get(destinatario=beto)
        self.assertEqual(cuenta.tope_mensual, Decimal("500000.00"))
        r = self.client.get(reverse("destinatarios:detalle", args=[beto.pk]))
        self.assertContains(r, "$500.000,00")
        self.assertNotContains(r, "Borrar")

    def datos_cuenta(self, d, **kw):
        datos = {
            "destinatario": d.pk,
            "alias": "beto.test",
            "tope_mensual": "500000,00",
            "tope_semanal": "150000,00",
            "minimo_por_pago": "10000,00",
            "estado": "activa",
        }
        datos.update(kw)
        return datos

    def test_post_con_minimo_mayor_que_el_tope_muestra_el_mensaje(self):
        self.client.force_login(self.admin)
        url = reverse("destinatarios:nueva_cuenta", args=[self.d.pk, "transferencia"])
        r = self.client.post(url, self.datos_cuenta(self.d, minimo_por_pago="500000,01"))
        self.assertContains(r, "El mínimo por pago no puede superar el tope mensual.")
        self.assertEqual(Cuenta.objects.count(), 0)

    def test_tipo_de_cuenta_no_habilitado_da_404(self):
        self.client.force_login(self.admin)
        url = reverse("destinatarios:nueva_cuenta", args=[self.d.pk, "otro"])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_lista_oculta_archivados_y_los_muestra_con_parametro(self):
        Destinatario.objects.filter(pk=self.d.pk).update(estado="archivado")
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse("destinatarios:lista")), "Ana Test")
        r = self.client.get(reverse("destinatarios:lista") + "?archivados=1")
        self.assertContains(r, "Ana Test")

    def test_us2_6_detalle_sin_borrar_ni_eliminar_y_sin_url_de_borrado(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("destinatarios:detalle", args=[self.d.pk]))
        self.assertNotContains(r, "Borrar")
        self.assertNotContains(r, "Eliminar")
        from django.urls import get_resolver

        nombres = [n for n in get_resolver().reverse_dict if isinstance(n, str)]
        self.assertFalse([n for n in nombres if "borrar" in n or "eliminar" in n])

    def test_estado_por_operador_403_y_get_405(self):
        c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.d,
            alias="a.b",
            tope_mensual=Decimal("1.00"),
        )
        urls = [
            reverse("destinatarios:estado_destinatario", args=[self.d.pk]),
            reverse("destinatarios:estado_cuenta", args=[c.pk]),
            reverse("destinatarios:desactivar_cuentas", args=[self.d.pk]),
        ]
        self.client.force_login(self.operador)
        for url in urls:
            self.assertEqual(self.client.post(url, {"estado": "inactivo"}).status_code, 403, url)
        self.client.force_login(self.admin)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 405, url)

    def _pos(self):
        from sistema.destinatarios.models import Proveedor

        return crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=self.d,
            titular="Ana Pérez",
            proveedor=Proveedor.objects.get(nombre="Mercadopago"),
            objetivo=Decimal("300000.00"),
        )

    def test_alta_pos_y_edicion_de_objetivo_por_post(self):
        from sistema.destinatarios.models import Proveedor

        self.client.force_login(self.admin)
        mp = Proveedor.objects.get(nombre="Mercadopago")
        url = reverse("destinatarios:nueva_cuenta", args=[self.d.pk, "terminal_pos"])
        datos = {
            "destinatario": self.d.pk,
            "titular": "Ana Pérez",
            "proveedor": mp.pk,
            "objetivo": "300000,00",
            "estado": "activa",
        }
        self.assertRedirects(
            self.client.post(url, datos), reverse("destinatarios:detalle", args=[self.d.pk])
        )
        c = Cuenta.objects.get(tipo="terminal_pos")
        self.assertEqual(c.objetivo, Decimal("300000.00"))
        r = self.client.get(reverse("destinatarios:detalle", args=[self.d.pk]))
        self.assertContains(r, "$300.000,00")
        self.assertContains(r, "Mercadopago")
        url = reverse("destinatarios:editar_cuenta", args=[c.pk])
        datos = {"destinatario": self.d.pk, "titular": "Ana Pérez", "objetivo": "250000,00"}
        self.client.post(url, datos)
        c.refresh_from_db()
        self.assertEqual(c.objetivo, Decimal("250000.00"))

    def test_alta_de_proveedor_y_repetido_rechazado(self):
        from sistema.destinatarios.models import Proveedor

        self.client.force_login(self.admin)
        url = reverse("destinatarios:proveedores")
        self.client.post(url, {"nombre": "Nuevo Prov"})
        self.assertTrue(Proveedor.objects.filter(nombre="Nuevo Prov").exists())
        r = self.client.post(url, {"nombre": "nuevo prov"})
        self.assertContains(r, "Ya existe un proveedor con ese nombre.")

    def test_operador_403_en_proveedores_y_editar_cuenta(self):
        c = self._pos()
        self.client.force_login(self.operador)
        for url in (
            reverse("destinatarios:proveedores"),
            reverse("destinatarios:editar_cuenta", args=[c.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 403, url)
            self.assertEqual(self.client.post(url, {}).status_code, 403, url)


class PantallasEfectivoTest(SistemaTestCase):
    setUp = PantallasTest.setUp

    def test_form_de_efectivo_sin_montos(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("destinatarios:nueva_cuenta", args=[self.d.pk, "efectivo"]))
        campos = r.context["form"].fields
        for monto in ("tope_mensual", "tope_semanal", "minimo_por_pago", "objetivo"):
            self.assertNotIn(monto, campos)

    def test_segunda_efectivo_activa_por_post_muestra_mensaje(self):
        self.client.force_login(self.admin)
        url = reverse("destinatarios:nueva_cuenta", args=[self.d.pk, "efectivo"])
        datos = {"destinatario": self.d.pk, "estado": "activa"}
        r = self.client.post(url, datos)
        self.assertRedirects(r, reverse("destinatarios:detalle", args=[self.d.pk]))
        r = self.client.post(url, datos)
        self.assertContains(r, "Ya hay una cuenta de efectivo activa.")
        self.assertEqual(Cuenta.objects.filter(tipo="efectivo").count(), 1)
