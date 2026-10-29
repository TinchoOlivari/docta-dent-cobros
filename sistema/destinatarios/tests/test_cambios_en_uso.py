from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from payment_instructions.models import User
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.forms import CuentaPosEdicionForm, CuentaTransferenciaEdicionForm
from sistema.destinatarios.models import Cuenta, Proveedor
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario, editar_cuenta

D = Decimal


class CambiosEnUsoTest(SistemaTestCase):
    def setUp(self):
        self.martin = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo", tipo="profesional"
        )
        self.jimena = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        self.c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.martin,
            alias="alias.dos",
            tope_mensual=D("500000.00"),
            tope_semanal=D("150000.00"),
            minimo_por_pago=D("10000.00"),
        )

    def test_us3_1_tope_actual_y_registro_nuevo_en_topes(self):
        editar_cuenta(self.c, usuario=None, tope_mensual=D("600000.00"))
        self.c.refresh_from_db()
        self.assertEqual(self.c.tope_mensual, D("600000.00"))
        nuevo = self.c.topes.filter(tipo="mensual").order_by("-desde").first()
        self.assertEqual(nuevo.monto, D("600000.00"))
        self.assertEqual(self.c.topes.filter(tipo="mensual").count(), 2)

    def test_tope_mensual_menor_que_minimo_rechazado_igual_ok(self):
        with self.assertRaises(ValidationError):
            editar_cuenta(self.c, usuario=None, tope_mensual=D("149999.99"))
        editar_cuenta(self.c, usuario=None, tope_mensual=D("150000.00"))

    def test_us3_2_cambiar_destinatario_no_toca_el_resto(self):
        antes = Cuenta.objects.get(pk=self.c.pk)
        editar_cuenta(self.c, usuario=None, destinatario=self.jimena)
        despues = Cuenta.objects.get(pk=self.c.pk)
        self.assertEqual(despues.destinatario, self.jimena)
        for campo in ("alias", "cbu", "tope_mensual", "tope_semanal", "minimo_por_pago", "estado"):
            self.assertEqual(getattr(despues, campo), getattr(antes, campo), campo)
        self.assertEqual(despues.fecha_activacion, antes.fecha_activacion)

    def test_editar_con_tipo_proveedor_o_estado_rechazado(self):
        mp = Proveedor.objects.get(nombre="Mercadopago")
        for datos in ({"tipo": "efectivo"}, {"proveedor": mp}, {"estado": "inactiva"}):
            with self.assertRaises(ValidationError):
                editar_cuenta(self.c, usuario=None, **datos)

    def test_forms_de_edicion_sin_tipo_proveedor_ni_estado(self):
        self.assertNotIn("tipo", CuentaTransferenciaEdicionForm().fields)
        self.assertNotIn("estado", CuentaTransferenciaEdicionForm().fields)
        for form in (CuentaTransferenciaEdicionForm(), CuentaPosEdicionForm()):
            self.assertNotIn("proveedor", form.fields)

    def test_fr11_post_con_otro_destinatario_cambia_solo_el_destinatario(self):
        admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.client.force_login(admin)
        datos = {
            "destinatario": self.jimena.pk,
            "titular": "",
            "titular_cuit": "",
            "banco": "",
            "alias": "alias.dos",
            "cbu": "",
            "tope_mensual": "500000,00",
            "tope_semanal": "150000,00",
            "minimo_por_pago": "10000,00",
        }
        r = self.client.post(reverse("destinatarios:editar_cuenta", args=[self.c.pk]), datos)
        self.assertRedirects(r, reverse("destinatarios:detalle", args=[self.jimena.pk]))
        c = Cuenta.objects.get(pk=self.c.pk)
        self.assertEqual(c.destinatario, self.jimena)
        self.assertEqual(c.tope_mensual, D("500000.00"))
        self.assertEqual(c.alias, "alias.dos")
        self.assertEqual(c.estado, "activa")
