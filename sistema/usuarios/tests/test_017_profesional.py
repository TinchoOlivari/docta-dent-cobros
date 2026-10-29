from datetime import datetime

from django.urls import reverse

from payment_instructions.models import User
from sistema.cobros.models import ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario
from sistema.usuarios.models import Perfil

URL = reverse("usuarios:profesional")


class BusquedaDelProfesionalTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.prof = User.objects.create_user(
            "dra", email="d@example.com", password="x", role="professional"
        )
        Perfil.objects.create(
            usuario=self.prof, id_profesional_dentalink=12, nombre_profesional_dentalink="Dra. X"
        )
        dest = crear_destinatario(usuario=None, nombre="Destino Secreto", tipo="clinica")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="secreto.alias",
            cbu="0000000000000000000009",
            tope_mensual="600000.00",
        )
        # 3 ítems de "Pérez Ejemplo" en agosto, septiembre y octubre; el de septiembre, cancelado.
        for mes, monto, estado in (
            (8, "20000.00", "pendiente"),
            (9, "10000.00", "cancelado"),
            (10, "20000.00", "pendiente"),
        ):
            self.item(mes, monto, estado, "Pérez Ejemplo", 12)
        self.item(10, "5000.00", "pendiente", "Otro Ejemplo", 12)
        self.item(10, "7000.00", "pendiente", "Pérez Ejemplo", 13)  # otro profesional

    def item(self, mes, monto, estado, paciente, id_prof):
        c = cobro_prueba(
            cuenta=self.cuenta,
            monto=monto,
            estado=estado,
            nombre_paciente=paciente,
            fecha=datetime(2026, mes, 15, 10, 0, tzinfo=ARG),
        )
        ItemCobro.objects.create(cobro=c, numero=1, monto=monto, id_profesional=id_prof)

    def test_q_muestra_todos_los_meses_y_el_total_sin_cancelados(self):
        self.client.force_login(self.prof)
        r = self.client.get(URL, {"q": "pérez"})
        self.assertEqual(len(r.context["pagina"].object_list), 3)
        self.assertEqual(str(r.context["total"]), "40000.00")
        self.assertContains(r, "$40.000,00")

    def test_q_vacio_usa_el_mes(self):
        self.client.force_login(self.prof)
        r = self.client.get(URL, {"q": "", "mes": "2026-10"})
        self.assertEqual(len(r.context["pagina"].object_list), 2)

    def test_no_muestra_datos_del_destinatario(self):
        self.client.force_login(self.prof)
        r = self.client.get(URL, {"q": "pérez"})
        for oculto in ("secreto.alias", "0000000000000000000009", "Destino Secreto"):
            self.assertNotContains(r, oculto)

    def test_sin_vinculo_da_vacio(self):
        Perfil.objects.filter(usuario=self.prof).update(
            id_profesional_dentalink=None, nombre_profesional_dentalink=""
        )
        self.client.force_login(self.prof)
        r = self.client.get(URL, {"q": "pérez"})
        self.assertEqual(str(r.context["total"]), "0.00")

    def test_operador_recibe_403(self):
        op = User.objects.create_user("op", email="o@example.com", role="operator")
        self.client.force_login(op)
        self.assertEqual(self.client.get(URL, {"q": "pérez"}).status_code, 403)
