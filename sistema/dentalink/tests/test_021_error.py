"""021 FR-049: el error de un rechazo de Dentalink llega como texto legible."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from payment_instructions.models import User
from sistema.avisos.models import DireccionAviso
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.dentalink import cliente, servicios
from sistema.dentalink.tests import cobro_con_items, copia_basica

CONEXION = "sistema.dentalink.cliente.http.client.HTTPSConnection"
PREFIJO = "Dentalink rechazó el pago: "


def _rechazo(cuerpo, status=400):
    with override_settings(DENTALINK_TOKEN="x"), patch(CONEXION) as clase:
        r = MagicMock(status=status)
        r.read.return_value = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
        clase.return_value.getresponse.return_value = r
        return cliente.crear_pago({"monto_pago": "1.00"})


class TextoDelErrorTest(SistemaTestCase):
    def test_error_json_muestra_el_texto(self):
        r = _rechazo({"error": "prestación ya pagada"})
        self.assertEqual(r.tipo, "rechazado")
        self.assertEqual(r.error, PREFIJO + "prestación ya pagada")

    def test_message_y_mensaje_muestran_el_texto(self):
        self.assertEqual(_rechazo({"message": "monto inválido"}).error, PREFIJO + "monto inválido")
        self.assertEqual(_rechazo({"mensaje": "sin banco"}).error, PREFIJO + "sin banco")

    def test_error_tiene_prioridad_sobre_message(self):
        self.assertEqual(_rechazo({"message": "b", "error": "a"}).error, PREFIJO + "a")

    def test_json_sin_clave_conocida_usa_el_cuerpo_crudo(self):
        self.assertEqual(_rechazo({"otro": "x"}).error, PREFIJO + '{"otro": "x"}')

    def test_html_de_500_caracteres_se_corta_a_200(self):
        r = _rechazo(b"<" + b"a" * 499)
        self.assertEqual(r.error, PREFIJO + "<" + "a" * 199)
        self.assertEqual(len(r.error) - len(PREFIJO), 200)

    def test_cuerpo_de_200_caracteres_no_se_corta(self):
        self.assertEqual(_rechazo(b"x" * 200).error, PREFIJO + "x" * 200)

    def test_cuerpo_vacio_deja_solo_el_prefijo(self):
        self.assertEqual(_rechazo(b"").error, PREFIJO)

    def test_error_de_servidor_no_cambia(self):
        self.assertEqual(_rechazo({"error": "x"}, 503).error, "Dentalink respondió 503.")


@override_settings(DENTALINK_TOKEN="x")
class LlegaAEnviosYAlEmailTest(SistemaTestCase):
    def test_el_texto_llega_a_envios_y_al_email(self):
        copia_basica()
        DireccionAviso.objects.create(email="aviso@example.com")
        User.objects.create_user("adm", email="a@example.com", password="x", role="administrator")
        item = cobro_con_items(["1000.00"]).items.get()
        with self.captureOnCommitCallbacks(execute=True), patch(CONEXION) as clase:
            r = MagicMock(status=422)
            r.read.return_value = json.dumps({"error": "prestación ya pagada"}).encode()
            clase.return_value.getresponse.return_value = r
            servicios.enviar_item(item.pk, ahora=datetime(2026, 10, 20, 12, 0, tzinfo=ARG))
        item.refresh_from_db()
        self.assertEqual(item.ultimo_error, PREFIJO + "prestación ya pagada")
        self.client.login(username="adm", password="x")
        r = self.client.get(reverse("dentalink:envios"))
        self.assertContains(r, PREFIJO + "prestación ya pagada")
        self.assertIn(PREFIJO + "prestación ya pagada", mail.outbox[0].body)
