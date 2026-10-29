from django.urls import reverse

from sistema.cobros.tests.test_complementario_pantallas import BasePantalla


class OcultarCamposSegunMetodoTest(BasePantalla):
    def test_servidor_rechaza_cuenta_con_efectivo(self):
        r = self.client.post(
            reverse("cobros:complementario_nuevo"),
            {
                "destinatario": self.jimena.pk,
                "mes": "2000-01",
                "monto": "1000,00",
                "metodo": "efectivo",
                "cuenta": self.cuenta.pk,
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "en efectivo, ninguna")
