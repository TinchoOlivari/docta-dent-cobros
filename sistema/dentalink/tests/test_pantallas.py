from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.dentalink.tests import cobro_con_items

A, OP, P = "administrator", "operator", "professional"
JUAN = {"id": 501, "nombre": "Juan Paciente", "dni": "30111222"}


def crear_usuarios():
    for rol in (A, OP, P):
        User.objects.create_user(rol, email=f"{rol}@example.com", password="x", role=rol)


class BusquedaTest(SistemaTestCase):
    def setUp(self):
        crear_usuarios()
        self.client.login(username=OP, password="x")
        self.url = reverse("dentalink:pacientes")

    @patch("sistema.dentalink.servicios.cliente")
    def test_busca_por_dni_y_muestra_el_paciente(self, cliente):
        cliente.buscar_pacientes.return_value = [JUAN]
        r = self.client.get(self.url, {"q": "30111222"})
        self.assertContains(r, "Juan Paciente")

    @patch("sistema.dentalink.servicios.cliente")
    def test_dos_coincidencias_se_listan(self, cliente):
        cliente.buscar_pacientes.return_value = [JUAN, {**JUAN, "id": 502, "nombre": "Juana Otra"}]
        r = self.client.get(self.url, {"q": "Juan"})
        self.assertContains(r, "Juan Paciente")
        self.assertContains(r, "Juana Otra")

    @patch("sistema.dentalink.servicios.cliente")
    def test_detalle_muestra_el_saldo_en_pesos(self, cliente):
        cliente.tratamientos.return_value = [
            {
                "id": 1,
                "nombre": "Ortodoncia",
                "id_profesional": 12,
                "profesionales": [{"id": 12, "nombre": "Dr. Y"}],
                "prestaciones": [
                    {
                        "id": 9001,
                        "id_prestacion": 9001,
                        "nombre": "Brackets",
                        "id_profesional": None,
                        "total": Decimal("100000.00"),
                        "pagado": Decimal("60000.00"),
                    }
                ],
            }
        ]
        r = self.client.get(reverse("dentalink:paciente", args=[501]))
        self.assertContains(r, "$40.000,00")


class PermisosBusquedaTest(SistemaTestCase):
    def setUp(self):
        crear_usuarios()

    @patch("sistema.dentalink.servicios.cliente")
    def test_matriz_por_rol(self, cliente):
        cliente.buscar_pacientes.return_value = []
        cliente.tratamientos.return_value = []
        urls = [reverse("dentalink:pacientes"), reverse("dentalink:paciente", args=[501])]
        for url in urls:
            for rol, codigo in {A: 200, OP: 200, P: 403}.items():
                with self.subTest(url=url, rol=rol):
                    self.client.logout()
                    self.client.login(username=rol, password="x")
                    self.assertEqual(self.client.get(url).status_code, codigo)
            with self.subTest(url=url, rol="anonimo"):
                self.client.logout()
                r = self.client.get(url)
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r["Location"].startswith("/sistema/login/?next="))


class PermisosConfiguracionTest(SistemaTestCase):
    def setUp(self):
        crear_usuarios()

    def pedir(self, rol, nombre, metodo):
        self.client.login(username=rol, password="x")
        return getattr(self.client, metodo)(reverse(nombre))

    @patch("sistema.dentalink.servicios.cliente")
    def test_permisos_por_rol(self, cliente):
        cliente.listar.return_value = []
        for nombre, metodo, esperado in [
            ("dentalink:configuracion", "get", 200),
            ("dentalink:configuracion", "post", 200),
            ("dentalink:actualizar", "post", 302),
        ]:
            self.assertEqual(self.pedir(A, nombre, metodo).status_code, esperado, nombre)
            for rol in (OP, P):
                self.assertEqual(self.pedir(rol, nombre, metodo).status_code, 403, (rol, nombre))

    def test_actualizar_por_get_es_405(self):
        self.assertEqual(self.pedir(A, "dentalink:actualizar", "get").status_code, 405)


class EnviosTest(SistemaTestCase):
    def setUp(self):
        crear_usuarios()
        self.client.login(username=A, password="x")
        cobro = cobro_con_items(["1000.00"] * 4)
        for n, estado in enumerate(["error", "revisar", "enviado", "requiere_ajuste"], start=1):
            cobro.items.filter(numero=n).update(estado_envio=estado)
        self.cobro = cobro
        self.url = reverse("dentalink:envios")

    def item(self, n):
        return self.cobro.items.get(numero=n)

    def test_sin_filtro_lista_error_y_revisar(self):
        r = self.client.get(self.url)
        refs = [i.referencia for i in r.context["pagina"]]
        self.assertCountEqual(refs, [self.item(1).referencia, self.item(2).referencia])

    def test_filtra_por_estado(self):
        r = self.client.get(self.url, {"estado": "requiere_ajuste"})
        self.assertEqual([i.pk for i in r.context["pagina"]], [self.item(4).pk])

    def test_post_marcar_enviado_redirige_y_cambia(self):
        item = self.item(1)
        r = self.client.post(reverse("dentalink:accion", args=[item.pk, "marcar-enviado"]))
        self.assertEqual(r.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "enviado")

    def test_accion_en_estado_invalido_avisa_y_no_cambia(self):
        item = self.item(3)
        r = self.client.post(reverse("dentalink:accion", args=[item.pk, "reintentar"]), follow=True)
        self.assertContains(r, "no se puede reintentar")
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "enviado")

    def test_accion_desconocida_o_item_inexistente_da_404(self):
        item = self.item(1)
        r = self.client.post(reverse("dentalink:accion", args=[item.pk, "borrar"]))
        self.assertEqual(r.status_code, 404)
        r = self.client.post(reverse("dentalink:accion", args=[999999, "reintentar"]))
        self.assertEqual(r.status_code, 404)

    def test_get_a_accion_da_405(self):
        r = self.client.get(reverse("dentalink:accion", args=[self.item(1).pk, "reintentar"]))
        self.assertEqual(r.status_code, 405)


class PermisosEnviosTest(SistemaTestCase):
    def setUp(self):
        crear_usuarios()
        self.item = cobro_con_items(["1000.00"]).items.get()
        self.item.estado_envio = "error"
        self.item.save()

    def urls(self):
        return [reverse("dentalink:envios")] + [
            reverse("dentalink:accion", args=[self.item.pk, a])
            for a in ("reintentar", "marcar-enviado", "marcar-ajustado")
        ]

    def llamar(self, url):
        if "envios/" in url and url.count("/") > 4:
            return self.client.post(url)
        return self.client.get(url)

    def test_administrador_accede(self):
        self.client.login(username=A, password="x")
        self.assertEqual(self.client.get(self.urls()[0]).status_code, 200)
        for url in self.urls()[1:]:
            self.assertEqual(self.client.post(url).status_code, 302)

    def test_operador_y_profesional_reciben_403(self):
        for rol in (OP, P):
            self.client.login(username=rol, password="x")
            for url in self.urls():
                self.assertEqual(self.llamar(url).status_code, 403, (rol, url))

    def test_sin_sesion_va_al_login(self):
        for url in self.urls():
            r = self.llamar(url)
            self.assertEqual(r.status_code, 302)
            self.assertIn("login", r.url)
