from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from payment_instructions.models import CuentaPosnet, Payment, Posnet, PosnetPaymentDetail, User
from payment_instructions.tests import BaseTestCase


class CreateQrPaymentViewTests(BaseTestCase):
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
        self.cuenta = CuentaPosnet.objects.create(nombre="Cuenta de prueba")
        self.posnet = Posnet.objects.create(
            nombre="Posnet de prueba",
            proveedor=Posnet.MERCADOPAGO,
            acepta_qr=True,
            cuenta_actual=self.cuenta,
        )
        self.client.login(username="operator1", password="pass")

    def _post(self, file_obj=None, **overrides):
        data = {
            "monto_cobrado": 550,
            "monto_recibido": 500,
            "posnet_id": self.posnet.id,
            "numero_operacion": "OP-1",
            "patient_name": "Paciente de prueba",
            "professional_id": self.professional.id,
        }
        data.update(overrides)
        if file_obj is not None:
            data["proof_of_payment_file"] = file_obj
        return self.client.post(reverse("payment_instructions:create_qr_payment"), data)

    def test_crea_pago_con_qr_sin_comprobante(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        payment = Payment.objects.get(pk=response.json()["payment_id"])
        self.assertEqual(payment.metodo, Payment.POSNET_QR)
        self.assertEqual(payment.estado, Payment.COMPLETADO)
        self.assertEqual(payment.amount, 500)
        self.assertIsNone(payment.payment_recipient)
        self.assertFalse(payment.proof_of_payment_file)

        self.assertEqual(PosnetPaymentDetail.objects.count(), 1)
        detalle = PosnetPaymentDetail.objects.get(payment=payment)
        self.assertEqual(detalle.cuenta, self.cuenta)
        self.assertEqual(detalle.monto_cobrado, 550)

    def test_crea_pago_con_qr_con_comprobante(self):
        file_obj = SimpleUploadedFile(
            "comprobante.png",
            (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
                b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
            content_type="image/png",
        )
        response = self._post(file_obj=file_obj)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        payment = Payment.objects.get(pk=response.json()["payment_id"])
        self.assertEqual(payment.estado, Payment.COMPLETADO)
        self.assertTrue(payment.proof_of_payment_file)

    def test_monto_cobrado_menor_a_monto_recibido_es_invalido(self):
        response = self._post(monto_cobrado=400, monto_recibido=500)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(PosnetPaymentDetail.objects.count(), 0)

    def test_monto_cero_es_invalido(self):
        response = self._post(monto_cobrado=0, monto_recibido=0)
        self.assertEqual(response.status_code, 400)

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

    def test_posnet_que_no_acepta_qr_es_invalido(self):
        self.posnet.acepta_qr = False
        self.posnet.save()
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertIn("no acepta", response.json()["error"])

    def test_form_solo_lista_posnets_que_aceptan_qr(self):
        posnet_sin_qr = Posnet.objects.create(
            nombre="Posnet sin QR", proveedor=Posnet.GETNET, acepta_qr=False
        )
        response = self.client.get(reverse("payment_instructions:create_qr_payment_form"))
        posnets_en_contexto = list(response.context["posnets"])
        self.assertIn(self.posnet, posnets_en_contexto)
        self.assertNotIn(posnet_sin_qr, posnets_en_contexto)

    def test_posnet_sin_cuenta_actual_es_invalido(self):
        posnet_sin_cuenta = Posnet.objects.create(
            nombre="Posnet sin cuenta",
            proveedor=Posnet.GETNET,
            acepta_qr=True,
        )
        response = self._post(posnet_id=posnet_sin_cuenta.id)
        self.assertEqual(response.status_code, 400)
        self.assertIn("cuenta logeada", response.json()["error"])

    def test_numero_operacion_vacio_es_invalido(self):
        response = self._post(numero_operacion="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("operación", response.json()["error"])

    def test_profesional_no_encontrado_es_invalido(self):
        response = self._post(professional_id=9999)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Profesional no encontrado", response.json()["error"])

    def test_sin_autenticacion_redirige(self):
        self.client.logout()
        response = self._post()
        self.assertEqual(response.status_code, 302)

    def test_rol_profesional_no_puede_acceder(self):
        self.client.logout()
        self.client.login(username="professional1", password="pass")
        response = self._post()
        self.assertNotEqual(response.status_code, 200)

    def test_reenvio_con_mismo_numero_de_operacion_es_rechazado(self):
        primera = self._post()
        self.assertTrue(primera.json()["success"])

        segunda = self._post()  # mismo posnet + mismo numero_operacion, a propósito
        self.assertEqual(segunda.status_code, 400)
        self.assertIn("Ya existe un pago", segunda.json()["error"])
        self.assertEqual(Payment.objects.filter(metodo=Payment.POSNET_QR).count(), 1)
