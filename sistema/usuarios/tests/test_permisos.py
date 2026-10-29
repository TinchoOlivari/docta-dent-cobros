from django.urls import reverse

from payment_instructions.models import User
from sistema.cobros.models import ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario
from sistema.usuarios.models import Perfil

A, OP, P = "administrator", "operator", "professional"


class PermisosTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        for rol in (A, OP, P):
            User.objects.create_user(rol, email=f"{rol}@example.com", password="x", role=rol)
        self.d = crear_destinatario(usuario=None, nombre="Ana Test", tipo="profesional")

    def codigo(self, rol, url):
        self.client.logout()
        if rol:
            self.client.login(username=rol, password="x")
        return self.client.get(url).status_code

    def test_matriz_de_pantallas_por_rol(self):
        esperado = {
            "usuarios:panel": {A: 200, OP: 403, P: 403},
            "usuarios:operador": {A: 200, OP: 200, P: 403},
            "usuarios:profesional": {A: 403, OP: 403, P: 200},
        }
        for nombre, por_rol in esperado.items():
            for rol, codigo in por_rol.items():
                with self.subTest(pantalla=nombre, rol=rol):
                    self.assertEqual(self.codigo(rol, reverse(nombre)), codigo)

    def test_profesional_con_mes_por_rol(self):
        # 404 al reporte de otro destinatario: ver reportes/tests/test_permisos.py
        url = reverse("usuarios:profesional") + "?mes=2026-10"
        self.assertEqual(
            [self.codigo(rol, url) for rol in (A, OP, P)],
            [403, 403, 200],
        )

    def test_anonimo_redirige_al_login_en_cada_pantalla(self):
        for nombre in ("panel", "operador", "profesional", "inicio"):
            with self.subTest(pantalla=nombre):
                r = self.client.get(reverse(f"usuarios:{nombre}"))
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r["Location"].startswith("/sistema/login/?next="))

    def test_destinatarios_y_proveedores_solo_para_administrador(self):
        urls = [
            reverse("destinatarios:lista"),
            reverse("destinatarios:proveedores"),
            reverse("destinatarios:detalle", args=[self.d.pk]),
        ]
        for url in urls:
            for rol, codigo in {A: 200, OP: 403, P: 403}.items():
                with self.subTest(url=url, rol=rol):
                    self.assertEqual(self.codigo(rol, url), codigo)


class UsuariosPantallasTest(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "jefa", email="jefa@example.com", password="x", role=A
        )
        User.objects.create_user("operator", email="operator@example.com", password="x", role=OP)
        User.objects.create_user(
            "professional", email="professional@example.com", password="x", role=P
        )
        self.u = User.objects.create_user("luis", email="luis@example.com", password="x", role=OP)

    def entrar(self, username):
        self.client.logout()
        if username:
            self.client.login(username=username, password="x")

    def urls(self):
        pk = self.u.pk
        return [
            reverse("usuarios:lista"),
            reverse("usuarios:nuevo"),
            reverse("usuarios:editar", args=[pk]),
            reverse("usuarios:activo", args=[pk]),
            reverse("usuarios:contrasena", args=[pk]),
        ]

    def test_pantallas_de_usuarios_dan_403_a_operador_y_profesional(self):
        for rol in (OP, P):
            self.entrar(rol)
            for url in self.urls():
                with self.subTest(rol=rol, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
                    self.assertEqual(self.client.post(url).status_code, 403)

    def test_administrador_entra_a_las_pantallas_de_usuarios(self):
        self.entrar("jefa")
        for url in self.urls():
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (200, 302, 405))

    def test_usuario_inexistente_da_404(self):
        self.entrar("jefa")
        self.assertEqual(self.client.get(reverse("usuarios:editar", args=[99999])).status_code, 404)

    def test_alta_y_desactivar_por_pantalla(self):
        self.entrar("jefa")
        r = self.client.post(
            reverse("usuarios:nuevo"),
            {
                "username": "ana",
                "first_name": "Ana",
                "last_name": "Test",
                "role": P,
                "password1": "Clave-9876!",
                "password2": "Clave-9876!",
            },
        )
        self.assertRedirects(r, reverse("usuarios:lista"))
        self.assertEqual(User.objects.get(username="ana").role, P)
        self.client.post(reverse("usuarios:activo", args=[self.u.pk]), {"activo": "0"})
        self.u.refresh_from_db()
        self.assertFalse(self.u.is_active)

    def test_desactivar_al_ultimo_admin_por_pantalla_muestra_error(self):
        self.entrar("jefa")
        r = self.client.post(
            reverse("usuarios:activo", args=[self.admin.pk]), {"activo": "0"}, follow=True
        )
        self.assertContains(r, "Tiene que quedar al menos un administrador activo.")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_cambiar_rol_del_ultimo_admin_por_pantalla_muestra_error(self):
        self.entrar("jefa")
        r = self.client.post(
            reverse("usuarios:editar", args=[self.admin.pk]),
            {"first_name": "", "last_name": "", "role": OP},
        )
        self.assertContains(r, "Tiene que quedar al menos un administrador activo.")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, A)

    def test_get_a_activo_da_405(self):
        self.entrar("jefa")
        self.assertEqual(
            self.client.get(reverse("usuarios:activo", args=[self.u.pk])).status_code, 405
        )

    def test_contrasena_por_pantalla(self):
        self.entrar("jefa")
        self.client.post(
            reverse("usuarios:contrasena", args=[self.u.pk]),
            {"new_password1": "Nueva-clave-1234!", "new_password2": "Nueva-clave-1234!"},
        )
        self.assertTrue(self.client.login(username="luis", password="Nueva-clave-1234!"))


class ProfesionalItemsTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.x = User.objects.create_user("dra_x", email="x@example.com", password="x", role=P)
        Perfil.objects.create(
            usuario=self.x, id_profesional_dentalink=12, nombre_profesional_dentalink="Dra. X"
        )
        dest = crear_destinatario(usuario=None, nombre="Destino Secreto", tipo="clinica")
        cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="alias.dos",
            cbu="0000000000000000000009",
            tope_mensual="60000.00",
        )
        cobro = cobro_prueba(cuenta=cuenta, monto="65000.00", nombre_paciente="Pablo Paciente")
        for numero, monto, id_prof, nombre in (
            (1, "40000.00", 12, "Dra. X"),
            (2, "25000.00", 13, "Dr. Y"),
        ):
            ItemCobro.objects.create(
                cobro=cobro,
                numero=numero,
                monto=monto,
                id_profesional=id_prof,
                nombre_profesional=nombre,
                nombre_tratamiento="Ortodoncia",
                nombre_prestacion=f"Prestación {numero}",
            )
        self.client.force_login(self.x)

    def test_us3_1_la_profesional_ve_solo_su_item(self):
        r = self.client.get(reverse("usuarios:profesional"))
        self.assertContains(r, "$40.000,00")
        self.assertNotContains(r, "$25.000,00")

    def test_us3_2_muestra_los_datos_y_oculta_la_cuenta(self):
        r = self.client.get(reverse("usuarios:profesional"))
        for dato in ("Pablo Paciente", "Ortodoncia", "Prestación 1", "Transferencia", "Pendiente"):
            self.assertContains(r, dato)
        for oculto in ("alias.dos", "0000000000000000000009", "Destino Secreto"):
            self.assertNotContains(r, oculto)

    def test_sin_vinculo_no_ve_ningun_item(self):
        Perfil.objects.filter(usuario=self.x).update(
            id_profesional_dentalink=None, nombre_profesional_dentalink=""
        )
        r = self.client.get(reverse("usuarios:profesional"))
        self.assertNotContains(r, "$40.000,00")
