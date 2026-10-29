from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase

LISTA = reverse("usuarios:lista")


class FiltrosUsuariosTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            "jefe", email="a@example.com", password="x", role="administrator"
        )
        for i in (1, 2, 3):
            User.objects.create_user(f"op{i}", email=f"o{i}@example.com", role="operator")
        User.objects.create_user(
            "prof1", email="p1@example.com", role="professional", first_name="Ejemplo"
        )
        User.objects.create_user("prof2", email="p2@example.com", role="professional")
        self.client.force_login(self.admin)

    def nombres(self, **params):
        r = self.client.get(LISTA, params)
        self.assertEqual(r.status_code, 200)
        return [u.username for u in r.context["usuarios"]]

    def test_filtro_por_rol(self):
        self.assertEqual(self.nombres(rol="operator"), ["op1", "op2", "op3"])

    def test_busqueda_por_usuario_nombre_o_apellido(self):
        self.assertEqual(self.nombres(q="ejemplo"), ["prof1"])
        self.assertEqual(self.nombres(q="OP"), ["op1", "op2", "op3"])

    def test_rol_invalido_no_filtra(self):
        self.assertEqual(len(self.nombres(rol="xyz")), 6)

    def test_q_y_rol_se_combinan_y_el_formulario_esta(self):
        self.assertEqual(self.nombres(q="1", rol="professional"), ["prof1"])

    def test_operador_y_profesional_reciben_403(self):
        for username in ("op1", "prof1"):
            self.client.force_login(User.objects.get(username=username))
            self.assertEqual(self.client.get(LISTA).status_code, 403, username)
