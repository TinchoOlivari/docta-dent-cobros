"""017/FR-007: el campo "Últimos 4" es numérico en el navegador; el servidor sigue validando igual."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from sistema.cobros.tests.test_pos import BasePos

ATRIBUTOS = ('inputmode="numeric"', 'pattern="[0-9]{4}"', 'maxlength="4"', 'autocomplete="off"')


class UltimosCuatroTest(BasePos):
    def assertAtributos(self, respuesta):
        html = respuesta.content.decode()
        i = html.index('name="ultimos_4"')
        etiqueta = html[html.rindex("<input", 0, i) : html.index(">", i)]
        for atributo in ATRIBUTOS:
            self.assertIn(atributo, etiqueta)

    def test_alta_sin_dentalink_tiene_los_tres_atributos(self):
        self.client.force_login(self.ana)
        self.assertAtributos(self.client.get(reverse("cobros:nuevo") + "?sin_dentalink=1"))

    def test_edicion_del_administrador_tiene_los_tres_atributos(self):
        cobro = self.pos()
        admin = get_user_model().objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        self.client.force_login(admin)
        self.assertAtributos(self.client.get(reverse("cobros:editar", args=[cobro.pk])))

    def test_debito_con_0042_se_acepta_y_con_42_o_12a4_se_rechaza(self):
        self.assertEqual(self.pos("debito", ultimos_4="0042").ultimos_4, "0042")
        for malo in ("42", "12a4"):
            with self.assertRaises(ValidationError):
                self.pos("debito", ultimos_4=malo, numero_operacion=malo)

    def test_qr_con_ultimos_4_vacio_se_acepta(self):
        self.assertIsNone(self.pos("qr", ultimos_4="").ultimos_4)
