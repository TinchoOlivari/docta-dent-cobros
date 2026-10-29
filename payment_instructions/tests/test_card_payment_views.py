from django.test import Client
from django.urls import reverse

from payment_instructions.models import (
    Banco,
    CuentaPosnet,
    Payment,
    Posnet,
    TarjetaPaymentDetail,
    User,
)
from payment_instructions.tests import BaseTestCase


class CreateCardPaymentViewTests(BaseTestCase):
    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="pro@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.banco = Banco.objects.create(nombre="Banco de prueba")
        self.cuenta = CuentaPosnet.objects.create(nombre="Cuenta de prueba")
        self.posnet = Posnet.objects.create(
            nombre="Posnet de prueba",
            proveedor=Posnet.MERCADOPAGO,
            acepta_credito=True,
            acepta_debito=True,
            cuenta_actual=self.cuenta,
        )
        self.client.login(username="operator1", password="pass")

    def _post(self, **overrides):
        data = {
            "monto_cobrado": 550,
            "monto_recibido": 500,
            "tipo_tarjeta": TarjetaPaymentDetail.CREDITO,
            "banco_id": self.banco.id,
            "posnet_id": self.posnet.id,
            "ultimos_4_digitos": "1234",
            "numero_operacion": "OP-1",
            "patient_name": "Paciente de prueba",
            "professional_id": self.professional.id,
        }
        data.update(overrides)
        return self.client.post(reverse("payment_instructions:create_card_payment"), data)

    def test_crea_pago_con_tarjeta_valido(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        self.assertEqual(Payment.objects.count(), 1)
        payment = Payment.objects.get(pk=response.json()["payment_id"])
        self.assertEqual(payment.metodo, Payment.POSNET_TARJETA)
        self.assertEqual(payment.estado, Payment.COMPLETADO)
        self.assertEqual(payment.amount, 500)
        self.assertIsNone(payment.payment_recipient)

        self.assertEqual(TarjetaPaymentDetail.objects.count(), 1)
        detalle = TarjetaPaymentDetail.objects.get(payment=payment)
        self.assertEqual(detalle.cuenta, self.cuenta)
        self.assertEqual(detalle.monto_cobrado, 550)

    def test_monto_cobrado_menor_a_monto_recibido_es_invalido(self):
        response = self._post(monto_cobrado=400, monto_recibido=500)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(TarjetaPaymentDetail.objects.count(), 0)

    def test_banco_inexistente_es_invalido(self):
        response = self._post(banco_id=9999)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Banco", response.json()["error"])

    def test_banco_inactivo_es_invalido(self):
        self.banco.is_active = False
        self.banco.save()
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Banco", response.json()["error"])

    def test_posnet_inexistente_es_invalido(self):
        response = self._post(posnet_id=9999)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Posnet", response.json()["error"])

    def test_posnet_inactivo_es_invalido(self):
        self.posnet.is_active = False
        self.posnet.save()
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Posnet", response.json()["error"])

    def test_posnet_sin_cuenta_actual_es_invalido(self):
        posnet_sin_cuenta = Posnet.objects.create(
            nombre="Posnet sin cuenta",
            proveedor=Posnet.GETNET,
            acepta_credito=True,
            acepta_debito=True,
        )
        response = self._post(posnet_id=posnet_sin_cuenta.id)
        self.assertEqual(response.status_code, 400)
        self.assertIn("cuenta logeada", response.json()["error"])

    def test_posnet_que_no_acepta_credito_es_invalido(self):
        self.posnet.acepta_credito = False
        self.posnet.save()
        response = self._post(tipo_tarjeta=TarjetaPaymentDetail.CREDITO)
        self.assertEqual(response.status_code, 400)
        self.assertIn("no acepta", response.json()["error"])

    def test_posnet_que_no_acepta_debito_es_invalido(self):
        self.posnet.acepta_debito = False
        self.posnet.save()
        response = self._post(tipo_tarjeta=TarjetaPaymentDetail.DEBITO)
        self.assertEqual(response.status_code, 400)
        self.assertIn("no acepta", response.json()["error"])

    def test_form_solo_lista_posnets_que_aceptan_credito_o_debito(self):
        posnet_sin_nada = Posnet.objects.create(nombre="Posnet sin nada", proveedor=Posnet.GETNET)
        response = self.client.get(reverse("payment_instructions:create_card_payment_form"))
        posnets_en_contexto = list(response.context["posnets"])
        self.assertIn(self.posnet, posnets_en_contexto)
        self.assertNotIn(posnet_sin_nada, posnets_en_contexto)

    def test_ultimos_4_digitos_con_letras_es_invalido(self):
        response = self._post(ultimos_4_digitos="12ab")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["field"], "ultimos_4_digitos")

    def test_ultimos_4_digitos_largo_incorrecto_es_invalido(self):
        response = self._post(ultimos_4_digitos="123")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["field"], "ultimos_4_digitos")

    def test_numero_operacion_vacio_es_invalido(self):
        response = self._post(numero_operacion="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("operación", response.json()["error"])

    def test_profesional_no_encontrado_es_invalido(self):
        response = self._post(professional_id=9999)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Profesional no encontrado", response.json()["error"])

    def test_profesional_requiere_permiso_de_operador(self):
        self.client.logout()
        response = self._post()
        # require_payment_creation_permission redirige si no está autenticado
        self.assertEqual(response.status_code, 302)

    def test_rol_profesional_no_puede_acceder(self):
        self.client.logout()
        self.client.login(username="professional1", password="pass")
        response = self._post()
        self.assertNotEqual(response.status_code, 200)

    def test_posnet_con_cuenta_desactivada_es_invalido(self):
        self.cuenta.is_active = False
        self.cuenta.save()
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertIn("cuenta logeada", response.json()["error"])

    def test_reenvio_con_mismo_numero_de_operacion_es_rechazado(self):
        primera = self._post()
        self.assertTrue(primera.json()["success"])

        segunda = self._post()  # mismo posnet + mismo numero_operacion, a propósito
        self.assertEqual(segunda.status_code, 400)
        self.assertIn("Ya existe un pago", segunda.json()["error"])
        self.assertEqual(Payment.objects.filter(metodo=Payment.POSNET_TARJETA).count(), 1)
