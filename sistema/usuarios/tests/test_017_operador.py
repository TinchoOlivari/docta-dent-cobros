from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from payment_instructions.models import User
from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

OPERADOR = reverse("usuarios:operador")


class PendientesDelOperadorTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.op = User.objects.create_user(
            "op", email="o@example.com", password="x", role="operator"
        )
        self.otro = User.objects.create_user("otra", email="t@example.com", role="operator")
        dest = crear_destinatario(usuario=None, nombre="Destinatario Ejemplo", tipo="clinica")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="ejemplo.uno",
            tope_mensual="1000000.00",
        )
        self.client.force_login(self.op)

    def cobro(self, usuario, dias, **campos):
        c = cobro_prueba(
            cuenta=self.cuenta,
            creado_por=usuario,
            monto="1000.00",
            nombre_paciente="Paciente Ejemplo",
            **campos,
        )
        Cobro.objects.filter(pk=c.pk).update(fecha_alta=timezone.now() - timedelta(days=dias))
        return c

    def test_cuenta_pendientes_visibles_del_operador(self):
        for dias in (1, 5, 14):
            self.cobro(self.op, dias)
        self.cobro(self.op, 16)  # fuera de la ventana de 15 días
        self.cobro(self.otro, 1)  # de otra operadora
        self.cobro(self.op, 1, metodo="efectivo", estado="finalizado")  # no pendiente
        r = self.client.get(OPERADOR)
        self.assertEqual(r.context["pendientes"], 3)

    def test_limite_de_la_ventana_14_y_16_dias(self):
        self.cobro(self.op, 14)
        self.cobro(self.op, 16)
        self.assertEqual(self.client.get(OPERADOR).context["pendientes"], 1)

    def test_administrador_ve_todos_los_pendientes(self):
        self.cobro(self.op, 30)
        self.cobro(self.otro, 1)
        admin = User.objects.create_user("adm", email="a@example.com", role="administrator")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(OPERADOR).context["pendientes"], 2)
