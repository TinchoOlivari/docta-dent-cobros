"""Contrato de error de los endpoints AJAX.

Un 400 por culpa de un campo tiene que decir cuál: el front usa "field" para marcar
ese input. Sin "field" el error cae en el aviso flotante y el operador tiene que
adivinar dónde se equivocó. Estos tests fijan ese contrato endpoint por endpoint.
"""

import json
import re
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from payment_instructions.models import (
    Banco,
    CuentaPosnet,
    Payment,
    PaymentRecipient,
    Posnet,
    TarjetaPaymentDetail,
    User,
)
from payment_instructions.tests import BaseTestCase
from payment_instructions.utils.validate_payment_amount import validate_payment_amount
from payment_instructions.views import SIN_DESTINATARIOS

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "payment_instructions"
VIEWS_FILE = Path(__file__).resolve().parent.parent / "views.py"


class ErrorContractTestsMixin:
    """Helpers compartidos: siempre se mira status, mensaje y campo juntos."""

    def assertFieldError(self, response, field):
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("error", payload)
        self.assertTrue(payload["error"], "el mensaje de error no puede estar vacío")
        self.assertEqual(payload.get("field"), field)

    def assertGeneralError(self, response):
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("error", payload)
        self.assertNotIn("field", payload)


class CashPaymentErrorFieldTests(ErrorContractTestsMixin, BaseTestCase):
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
        self.client.login(username="operator1", password="pass")

    def _post(self, **overrides):
        data = {
            "amount": 1000,
            "patient_name": "Paciente de prueba",
            "professional_id": self.professional.id,
        }
        data.update(overrides)
        return self.client.post(reverse("payment_instructions:create_cash_payment"), data)

    def test_monto_vacio_devuelve_campo_amount(self):
        self.assertFieldError(self._post(amount=""), "amount")

    def test_monto_con_letras_devuelve_campo_amount(self):
        self.assertFieldError(self._post(amount="mil"), "amount")

    def test_monto_cero_devuelve_campo_amount(self):
        self.assertFieldError(self._post(amount=0), "amount")

    def test_monto_negativo_devuelve_campo_amount(self):
        self.assertFieldError(self._post(amount=-1), "amount")

    def test_monto_uno_es_valido(self):
        # Límite de "mayor a cero": uno arriba del rechazo tiene que pasar.
        response = self._post(amount=1)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    def test_paciente_vacio_devuelve_campo_patient_name(self):
        self.assertFieldError(self._post(patient_name="   "), "patient_name")

    def test_paciente_corto_devuelve_campo_patient_name(self):
        self.assertFieldError(self._post(patient_name="Ab"), "patient_name")

    def test_profesional_vacio_devuelve_campo_professional_id(self):
        self.assertFieldError(self._post(professional_id=""), "professional_id")

    def test_profesional_inexistente_devuelve_campo_professional_id(self):
        self.assertFieldError(self._post(professional_id=999999), "professional_id")

    def test_profesional_no_numerico_devuelve_campo_professional_id(self):
        self.assertFieldError(self._post(professional_id="abc"), "professional_id")


class CardPaymentErrorFieldTests(ErrorContractTestsMixin, BaseTestCase):
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

    def test_monto_cobrado_invalido_apunta_a_monto_cobrado(self):
        self.assertFieldError(self._post(monto_cobrado="x"), "monto_cobrado")

    def test_monto_recibido_invalido_apunta_a_monto_recibido(self):
        # El monto cobrado está bien: el error no puede señalar el campo equivocado.
        self.assertFieldError(self._post(monto_recibido="x"), "monto_recibido")

    def test_monto_recibido_cero_apunta_a_monto_recibido(self):
        self.assertFieldError(self._post(monto_recibido=0), "monto_recibido")

    def test_cobrado_menor_que_recibido_apunta_a_monto_cobrado(self):
        self.assertFieldError(self._post(monto_cobrado=400, monto_recibido=500), "monto_cobrado")

    def test_cobrado_igual_a_recibido_es_valido(self):
        # Límite: igual pasa, uno menos no.
        response = self._post(monto_cobrado=500, monto_recibido=500)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    def test_tipo_de_tarjeta_invalido_devuelve_campo_tipo_tarjeta(self):
        self.assertFieldError(self._post(tipo_tarjeta="CHEQUE"), "tipo_tarjeta")

    def test_banco_inexistente_devuelve_campo_banco_id(self):
        self.assertFieldError(self._post(banco_id=999999), "banco_id")

    def test_posnet_inexistente_devuelve_campo_posnet_id(self):
        self.assertFieldError(self._post(posnet_id=999999), "posnet_id")

    def test_posnet_que_no_acepta_el_tipo_devuelve_campo_posnet_id(self):
        self.posnet.acepta_debito = False
        self.posnet.save(update_fields=["acepta_debito"])
        self.assertFieldError(self._post(tipo_tarjeta=TarjetaPaymentDetail.DEBITO), "posnet_id")

    def test_posnet_sin_cuenta_logeada_devuelve_campo_posnet_id(self):
        self.posnet.cuenta_actual = None
        self.posnet.save(update_fields=["cuenta_actual"])
        self.assertFieldError(self._post(), "posnet_id")

    def test_ultimos_4_digitos_cortos_devuelven_campo_ultimos_4_digitos(self):
        self.assertFieldError(self._post(ultimos_4_digitos="123"), "ultimos_4_digitos")

    def test_ultimos_4_digitos_con_letras_devuelven_campo_ultimos_4_digitos(self):
        self.assertFieldError(self._post(ultimos_4_digitos="12a4"), "ultimos_4_digitos")

    def test_numero_de_operacion_vacio_devuelve_campo_numero_operacion(self):
        self.assertFieldError(self._post(numero_operacion="  "), "numero_operacion")

    def test_numero_de_operacion_duplicado_devuelve_campo_numero_operacion(self):
        self.assertEqual(self._post().status_code, 200)
        self.assertFieldError(self._post(), "numero_operacion")


class LinkPaymentErrorFieldTests(ErrorContractTestsMixin, BaseTestCase):
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
            acepta_link_pago=True,
            cuenta_actual=self.cuenta,
        )
        self.client.login(username="operator1", password="pass")

    def _post(self, archivo="omitido", **overrides):
        data = {
            "monto_cobrado": 550,
            "monto_recibido": 500,
            "posnet_id": self.posnet.id,
            "numero_operacion": "OP-1",
            "patient_name": "Paciente de prueba",
            "professional_id": self.professional.id,
        }
        data.update(overrides)
        if archivo != "omitido" and archivo is not None:
            data["proof_of_payment_file"] = archivo
        return self.client.post(reverse("payment_instructions:create_link_payment"), data)

    def test_comprobante_faltante_devuelve_campo_del_comprobante(self):
        self.assertFieldError(self._post(), "proof_of_payment_file")

    def test_comprobante_demasiado_grande_devuelve_campo_del_comprobante(self):
        grande = SimpleUploadedFile(
            "comprobante.png", b"x" * (5 * 1024 * 1024 + 1), content_type="image/png"
        )
        self.assertFieldError(self._post(archivo=grande), "proof_of_payment_file")

    def test_tipo_de_comprobante_no_permitido_devuelve_campo_del_comprobante(self):
        texto = SimpleUploadedFile("comprobante.txt", b"contenido", content_type="text/plain")
        self.assertFieldError(self._post(archivo=texto), "proof_of_payment_file")

    def test_posnet_que_no_acepta_link_devuelve_campo_posnet_id(self):
        self.posnet.acepta_link_pago = False
        self.posnet.save(update_fields=["acepta_link_pago"])
        archivo = SimpleUploadedFile("comprobante.png", b"contenido", content_type="image/png")
        self.assertFieldError(self._post(archivo=archivo), "posnet_id")


class TransferenciaErrorFieldTests(ErrorContractTestsMixin, BaseTestCase):
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
        self.client.login(username="operator1", password="pass")

    def _post(self, **overrides):
        data = {
            "amount": 1000,
            "patient_name": "Paciente de prueba",
            "professional_id": self.professional.id,
        }
        data.update(overrides)
        return self.client.post(
            reverse("payment_instructions:iniciar_transferencia"),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_monto_faltante_devuelve_campo_amount(self):
        self.assertFieldError(self._post(amount=None), "amount")

    def test_monto_con_letras_devuelve_campo_amount(self):
        self.assertFieldError(self._post(amount="mil"), "amount")

    def test_sin_destinatarios_no_apunta_a_ningun_campo(self):
        # No hay destinatario configurado: el operador no tiene nada que corregir en el
        # formulario, así que el error va al aviso general y no marca un input.
        self.assertGeneralError(self._post())

    def test_pago_inexistente_al_cancelar_no_apunta_a_ningun_campo(self):
        response = self.client.post(
            reverse("payment_instructions:cancel_payment"),
            data=json.dumps({"payment_id": 999999, "motivo": "OTRO"}),
            content_type="application/json",
        )
        self.assertGeneralError(response)


class FieldNameCoverageTests(BaseTestCase):
    """Un "field" que ningún template sabe traducir es un error invisible.

    El front cae al aviso flotante cuando no encuentra el input, así que el bug no
    rompe nada a la vista: simplemente el campo deja de marcarse y nadie se entera.
    Por eso se chequea acá.

    Alcance: solo los campos escritos como literal en views.py. Los que salen de
    _validation_error_response son nombres de campo del modelo y no pasan por acá;
    para esos el front cae al aviso flotante a propósito."""

    FORMULARIOS = [
        "cash_payment_form.html",
        "card_payment_form.html",
        "link_payment_form.html",
        "qr_payment_form.html",
        "regular_payment_form.html",
    ]

    def _campos_que_emiten_las_views(self):
        fuente = VIEWS_FILE.read_text(encoding="utf-8")
        campos = set(re.findall(r'"field":\s*"([a-z0-9_]+)"', fuente))
        # PROOF_FIELD se pasa por constante, no como literal en el sitio del error.
        if "PROOF_FIELD" in fuente:
            campos.add("proof_of_payment_file")
        return campos

    def _campos_de_un_template(self, nombre):
        contenido = (TEMPLATES_DIR / nombre).read_text(encoding="utf-8")
        bloque = re.search(r"window\.DD_FIELD_IDS = \{(.*?)\};", contenido, re.DOTALL)
        self.assertIsNotNone(bloque, f"{nombre} no declara window.DD_FIELD_IDS")
        return set(re.findall(r"([a-z0-9_]+):\s*'", bloque.group(1)))

    def test_todo_campo_que_emiten_las_views_lo_traduce_algun_formulario(self):
        declarados = set()
        for nombre in self.FORMULARIOS:
            declarados |= self._campos_de_un_template(nombre)

        emitidos = self._campos_que_emiten_las_views()
        self.assertTrue(emitidos, "las views no emiten ningún campo: el contrato se perdió")

        # tipo_tarjeta se elige con un toggle, no con un input: no hay id que marcar y
        # el error cae al aviso flotante a propósito.
        sin_input = {"tipo_tarjeta"}
        self.assertEqual(emitidos - declarados - sin_input, set())

    def test_cada_id_declarado_existe_en_su_propio_formulario(self):
        for nombre in self.FORMULARIOS:
            contenido = (TEMPLATES_DIR / nombre).read_text(encoding="utf-8")
            bloque = re.search(r"window\.DD_FIELD_IDS = \{(.*?)\};", contenido, re.DOTALL)
            for campo, elemento_id in re.findall(r"([a-z0-9_]+):\s*'([^']+)'", bloque.group(1)):
                self.assertIn(
                    f'id="{elemento_id}"',
                    contenido,
                    f"{nombre}: {campo} apunta a #{elemento_id}, que no existe en la pantalla",
                )

    def test_cada_formulario_incluye_el_partial_de_errores(self):
        for nombre in self.FORMULARIOS:
            contenido = (TEMPLATES_DIR / nombre).read_text(encoding="utf-8")
            self.assertIn("payment_instructions/_form_errors.html", contenido, nombre)


class FormRenderTests(BaseTestCase):
    """Las pantallas que todavía no tenían test de render.

    El aviso flotante se incluye al final del body, fuera de los {% if %} del form de
    transferencia: tiene que existir también cuando el pendiente ya no está."""

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
        self.client.login(username="operator1", password="pass")

    def test_form_de_efectivo_renderiza_el_aviso(self):
        response = self.client.get(reverse("payment_instructions:create_cash_payment_form"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ddSnackbar"')

    def test_form_de_transferencia_renderiza_el_aviso(self):
        response = self.client.get(reverse("payment_instructions:create_regular_payment_form"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ddSnackbar"')

    def test_lista_de_pendientes_renderiza_el_aviso(self):
        # Cancelar desde la lista solo borraba la fila, sin confirmar nada.
        response = self.client.get(reverse("payment_instructions:pending_transfers"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ddSnackbar"')

    def test_lista_vacia_muestra_el_mensaje_y_esconde_la_tabla(self):
        response = self.client.get(reverse("payment_instructions:pending_transfers"))

        self.assertContains(response, 'id="listaPendientes" class="hidden"')
        self.assertNotIn(
            "hidden", self._clase_de(response, "sinPendientes"), "el mensaje de vacío no se ve"
        )

    def test_con_pendientes_muestra_la_tabla_y_esconde_el_mensaje(self):
        # Los dos bloques se renderizan siempre: el JavaScript los intercambia cuando se
        # cancela la última fila sin recargar la página.
        recipient = PaymentRecipient.objects.create(
            name="Destinatario de prueba",
            alias="destinatario.de.prueba",
            cbu="1" * 22,
            max_amount=100000,
            priority_order=1,
        )
        Payment.objects.create(
            amount=1000,
            payment_type=Payment.REGULAR,
            estado=Payment.PENDIENTE,
            payment_recipient=recipient,
            patient_name="Paciente de prueba",
            professional=self.professional,
            operator_user=self.operator,
        )

        response = self.client.get(reverse("payment_instructions:pending_transfers"))

        self.assertContains(response, 'id="listaPendientes"')
        self.assertNotIn('id="listaPendientes" class="hidden"', response.content.decode())
        self.assertIn("hidden", self._clase_de(response, "sinPendientes"))

    def _clase_de(self, response, element_id):
        html = response.content.decode()
        trozo = html[html.index(f'id="{element_id}"') :]
        return trozo[: trozo.index(">")]

    def test_pendiente_inexistente_igual_renderiza_el_aviso(self):
        response = self.client.get(
            reverse("payment_instructions:resume_transfer_payment_form", args=[999999])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ddSnackbar"')


class MensajeSinDestinatariosTests(BaseTestCase):
    """SIN_DESTINATARIOS compara contra un texto que vive en otro módulo.

    Si alguien reescribe el mensaje en validate_payment_amount, la comparación falla
    en silencio y el error empieza a marcar el campo del monto, que es justo lo que no
    tiene que pasar: no hay nada que corregir ahí."""

    def test_el_texto_de_la_view_y_el_de_la_utilidad_son_el_mismo(self):
        es_valido, mensaje, recipient = validate_payment_amount(1000)

        self.assertFalse(es_valido)
        self.assertIsNone(recipient)
        self.assertEqual(mensaje, SIN_DESTINATARIOS)


class DepositErrorFieldTests(ErrorContractTestsMixin, BaseTestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self.professional = User.objects.create_user(
            username="professional1",
            email="pro@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        PaymentRecipient.objects.create(
            name="Cuenta de señas",
            alias="cuenta.de.senias",
            cbu="9" * 22,
            recipient_type=PaymentRecipient.DEPOSIT_ONLY,
            max_amount=1,
            priority_order=1,
        )
        self.client.login(username="operator1", password="pass")

    def _post(self, **overrides):
        data = {
            "amount": 5000,
            "patient_name": "Paciente de prueba",
            "professional_id": self.professional.id,
            "proof_of_payment_file": SimpleUploadedFile(
                "comprobante.png", b"contenido", content_type="image/png"
            ),
        }
        data.update(overrides)
        return self.client.post(reverse("payment_instructions:create_deposit"), data)

    def test_monto_con_letras_devuelve_campo_amount(self):
        # Antes caía en el except general y devolvía el texto crudo de Python.
        response = self._post(amount="mil")
        self.assertFieldError(response, "amount")
        self.assertNotIn("invalid literal", response.json()["error"])

    def test_monto_cero_devuelve_campo_amount(self):
        self.assertFieldError(self._post(amount=0), "amount")

    def test_comprobante_faltante_devuelve_campo_del_comprobante(self):
        data = {
            "amount": 5000,
            "patient_name": "Paciente de prueba",
            "professional_id": self.professional.id,
        }
        response = self.client.post(reverse("payment_instructions:create_deposit"), data)
        self.assertFieldError(response, "proof_of_payment_file")


class ReglasDeValidacionClienteTests(BaseTestCase):
    """Las reglas del JavaScript apuntan a campos por nombre, igual que el backend.

    Un nombre mal escrito no rompe nada a la vista: showFieldError no encuentra el
    input, devuelve false y validarCampos manda el mensaje al aviso flotante. El campo
    deja de marcarse y nadie se entera. Por eso se chequea acá."""

    FORMULARIOS = FieldNameCoverageTests.FORMULARIOS

    def test_toda_regla_del_cliente_apunta_a_un_campo_declarado(self):
        for nombre in self.FORMULARIOS:
            contenido = (TEMPLATES_DIR / nombre).read_text(encoding="utf-8")
            bloque = re.search(r"window\.DD_FIELD_IDS = \{(.*?)\};", contenido, re.DOTALL)
            declarados = set(re.findall(r"([a-z0-9_]+):\s*'", bloque.group(1)))

            usados = set(re.findall(r"field:\s*'([a-z0-9_]+)'", contenido))
            usados |= set(re.findall(r"showFieldError\('([a-z0-9_]+)'", contenido))

            self.assertTrue(usados, f"{nombre} no declara ninguna regla de validación")
            self.assertEqual(
                usados - declarados,
                set(),
                f"{nombre}: reglas que apuntan a un campo que el formulario no traduce",
            )
