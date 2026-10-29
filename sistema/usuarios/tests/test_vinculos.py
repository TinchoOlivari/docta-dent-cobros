from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.dentalink.models import Profesional
from sistema.destinatarios.servicios import crear_destinatario, editar_destinatario
from sistema.usuarios import roles
from sistema.usuarios.models import Perfil
from sistema.usuarios.servicios import vincular_profesional


class VinculosTest(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "jefa", email="jefa@example.com", password="x", role=roles.ADMIN
        )
        self.drax = User.objects.create_user(
            "drax", email="drax@example.com", password="x", role=roles.PROFESSIONAL
        )
        self.op = User.objects.create_user(
            "luis", email="luis@example.com", password="x", role=roles.OPERATOR
        )

    def vincular(self, u=None, id_=101, nombre="Dra. X"):
        return vincular_profesional(
            u or self.drax,
            usuario=self.admin,
            id_profesional_dentalink=id_,
            nombre_profesional_dentalink=nombre,
        )

    def test_vincular_profesional_se_ve_en_su_pantalla(self):
        self.vincular()
        self.assertEqual(self.drax.perfil.id_profesional_dentalink, 101)
        self.client.login(username="drax", password="x")

    def test_desvincular_con_id_nulo_y_nombre_vacio(self):
        self.vincular()
        p = self.vincular(id_=None, nombre="")
        self.assertIsNone(p.id_profesional_dentalink)

    def test_vincular_operador_falla(self):
        with self.assertRaisesMessage(ValidationError, "Solo se vincula un usuario Profesional."):
            self.vincular(self.op)

    def test_id_sin_nombre_falla(self):
        with self.assertRaises(ValidationError):
            self.vincular(nombre="")

    def test_nombre_sin_id_falla(self):
        with self.assertRaises(ValidationError):
            self.vincular(id_=None)

    def test_constraint_id_sin_nombre(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Perfil.objects.create(usuario=self.drax, id_profesional_dentalink=101)

    def test_editar_usuario_guarda_el_vinculo(self):
        r = self.client.login(username="jefa", password="x")
        r = self.client.post(
            reverse("usuarios:editar", args=[self.drax.pk]),
            {
                "first_name": "D",
                "last_name": "X",
                "role": roles.PROFESSIONAL,
                "profesional_dentalink": Profesional.objects.create(
                    id_dentalink=101, nombre="Dra. X"
                ).pk,
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.drax.perfil.nombre_profesional_dentalink, "Dra. X")

    def test_editar_usuario_con_profesional_inexistente_no_cambia_nada(self):
        self.client.login(username="jefa", password="x")
        r = self.client.post(
            reverse("usuarios:editar", args=[self.drax.pk]),
            {"first_name": "Nuevo", "role": roles.PROFESSIONAL, "profesional_dentalink": "999999"},
        )
        self.assertEqual(r.status_code, 200)
        self.drax.refresh_from_db()
        self.assertEqual(self.drax.first_name, "")
        self.assertFalse(Perfil.objects.exists())


class VinculoDestinatarioTest(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "jefa", email="jefa@example.com", password="x", role=roles.ADMIN
        )
        self.drax = User.objects.create_user(
            "drax", email="drax@example.com", password="x", role=roles.PROFESSIONAL
        )
        self.op = User.objects.create_user(
            "luis", email="luis@example.com", password="x", role=roles.OPERATOR
        )
        self.d = crear_destinatario(usuario=self.admin, nombre="Dra. X", tipo="profesional")
        self.otro = crear_destinatario(usuario=self.admin, nombre="Otro Test", tipo="proveedor")

    def test_editar_destinatario_guarda_vinculo(self):
        editar_destinatario(self.d, usuario=self.admin, usuario_profesional=self.drax)
        self.assertEqual(self.drax.destinatario, self.d)

    def test_vincular_operador_falla(self):
        with self.assertRaisesMessage(ValidationError, "Solo se vincula un usuario Profesional."):
            editar_destinatario(self.d, usuario=self.admin, usuario_profesional=self.op)

    def test_segundo_destinatario_con_mismo_usuario_falla(self):
        editar_destinatario(self.d, usuario=self.admin, usuario_profesional=self.drax)
        with self.assertRaises(ValidationError):
            editar_destinatario(self.otro, usuario=self.admin, usuario_profesional=self.drax)

    def test_destinatario_no_se_borra(self):
        with self.assertRaises(ProtectedError):
            self.d.delete()

    def test_profesional_ve_su_destinatario_y_no_otro(self):
        editar_destinatario(self.d, usuario=self.admin, usuario_profesional=self.drax)
        self.client.login(username="drax", password="x")
        r = self.client.get("/sistema/profesional/")
        self.assertContains(r, "Dra. X")
        self.assertNotContains(r, "Otro Test")
        r = self.client.get(reverse("destinatarios:detalle", args=[self.d.pk]))
        self.assertEqual(r.status_code, 403)

    def test_form_ofrece_solo_profesionales_activos(self):
        from sistema.destinatarios.forms import DestinatarioForm

        qs = DestinatarioForm(instance=self.d).fields["usuario_profesional"].queryset
        self.assertEqual(list(qs), [self.drax])
