from django.core.exceptions import ValidationError
from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.usuarios import roles
from sistema.usuarios.servicios import (
    cambiar_activo,
    crear_usuario,
    editar_usuario,
    poner_contrasena,
)

ULTIMO = "Tiene que quedar al menos un administrador activo."


class UsuariosServiciosTest(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "jefa", email="jefa@example.com", password="x", role=roles.ADMIN
        )

    def nuevo(self, username="luis", role=roles.OPERATOR):
        return crear_usuario(
            usuario=self.admin, username=username, password="Clave-9876!", role=role
        )

    def test_crear_operador_entra_a_su_pantalla(self):
        u = self.nuevo()
        self.assertFalse(u.is_superuser)
        self.assertFalse(u.is_staff)
        self.client.post(reverse("usuarios:login"), {"username": "luis", "password": "Clave-9876!"})
        self.assertEqual(self.client.get("/sistema/operador/").status_code, 200)

    def test_username_repetido_falla(self):
        self.nuevo()
        with self.assertRaisesMessage(ValidationError, "Ya existe un usuario con ese nombre."):
            self.nuevo()

    def test_rol_invalido_falla(self):
        with self.assertRaisesMessage(ValidationError, "Rol inválido."):
            self.nuevo(role="superuser")

    def test_editar_campo_ajeno_falla(self):
        u = self.nuevo()
        with self.assertRaises(ValidationError):
            editar_usuario(u, usuario=self.admin, is_superuser=True)

    def test_cambiar_rol_de_operador_a_administrador(self):
        u = self.nuevo()
        editar_usuario(u, usuario=self.admin, role=roles.ADMIN)
        u.refresh_from_db()
        self.assertEqual(u.role, roles.ADMIN)

    def test_unico_admin_no_se_desactiva_ni_cambia_de_rol(self):
        with self.assertRaisesMessage(ValidationError, ULTIMO):
            cambiar_activo(self.admin, False, usuario=self.admin)
        with self.assertRaisesMessage(ValidationError, ULTIMO):
            editar_usuario(self.admin, usuario=self.admin, role=roles.OPERATOR)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertEqual(self.admin.role, roles.ADMIN)

    def test_con_dos_admins_se_desactiva_uno_y_el_segundo_falla(self):
        otro = self.nuevo("otra", roles.ADMIN)
        cambiar_activo(otro, False, usuario=self.admin)
        with self.assertRaisesMessage(ValidationError, ULTIMO):
            cambiar_activo(self.admin, False, usuario=self.admin)

    def test_admin_desactivado_no_cuenta(self):
        otro = self.nuevo("otra", roles.ADMIN)
        cambiar_activo(otro, False, usuario=self.admin)
        with self.assertRaisesMessage(ValidationError, ULTIMO):
            editar_usuario(self.admin, usuario=self.admin, role=roles.PROFESSIONAL)

    def test_desactivar_admin_ya_desactivado_con_un_solo_activo_no_falla(self):
        otro = self.nuevo("otra", roles.ADMIN)
        cambiar_activo(otro, False, usuario=self.admin)
        cambiar_activo(otro, False, usuario=self.admin)

    def test_desactivado_no_entra_y_sigue_en_la_tabla(self):
        u = self.nuevo()
        cambiar_activo(u, False, usuario=self.admin)
        ok = self.client.login(username="luis", password="Clave-9876!")
        self.assertFalse(ok)
        self.assertTrue(User.objects.filter(username="luis").exists())

    def test_contrasena_nueva_reemplaza_la_vieja(self):
        u = self.nuevo()
        poner_contrasena(u, "Otra-clave-5432!", usuario=self.admin)
        self.assertFalse(self.client.login(username="luis", password="Clave-9876!"))
        self.assertTrue(self.client.login(username="luis", password="Otra-clave-5432!"))
