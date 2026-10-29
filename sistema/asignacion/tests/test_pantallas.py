from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.asignacion.tests.test_asignar import DatosEjemplo, INICIO_MES
from sistema.destinatarios.models import Cuenta

from decimal import Decimal as D


class PantallasTest(DatosEjemplo):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse("asignacion:lista")

    def mover(self, cuenta, posicion):
        return self.client.post(
            reverse("asignacion:mover", args=[cuenta.pk]), {"posicion": posicion}, follow=True
        )

    def mensajes(self, r):
        return [str(m) for m in r.context["messages"]]

    def test_lista_muestra_topes_y_cupo(self):
        cobro_prueba(cuenta=self.c1, monto=D("450000.00"), estado="finalizado", fecha=INICIO_MES)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "$500.000,00")
        self.assertContains(r, "Sin tope")
        self.assertContains(r, "$50.000,00")

    def test_lista_barra_con_tono_y_sin_porcentaje(self):  # 022 FR-023: 315.000 de 500.000
        cobro_prueba(cuenta=self.c1, monto=D("315000.00"), estado="finalizado", fecha=INICIO_MES)
        html = self.client.get(self.url).content.decode()
        self.assertIn("$315.000,00 de $500.000,00", html)

    def test_mover_rodrigo_a_1(self):
        r = self.mover(self.c3, 1)
        self.assertRedirects(r, self.url)
        self.assertEqual(Cuenta.objects.get(pk=self.c3.pk).posicion, 1)
        self.assertIn("Lista de prioridad actualizada.", self.mensajes(r))

    def test_posicion_9_muestra_error(self):
        r = self.mover(self.c3, 9)
        self.assertIn("La posición debe ir de 1 a 3.", self.mensajes(r))
        self.assertEqual(Cuenta.objects.get(pk=self.c3.pk).posicion, 3)

    def test_posicion_no_numerica_muestra_error(self):
        r = self.mover(self.c3, "abc")
        self.assertIn("Escribí un número de posición.", self.mensajes(r))

    def test_get_a_mover_es_405(self):
        self.assertEqual(
            self.client.get(reverse("asignacion:mover", args=[self.c3.pk])).status_code, 405
        )


class PermisosTest(DatosEjemplo):
    def urls(self):
        return [reverse("asignacion:lista"), reverse("asignacion:mover", args=[self.c1.pk])]

    def test_sin_sesion_va_al_login(self):
        for url in self.urls():
            r = self.client.post(url, {"posicion": 1})
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login/", r["Location"])

    def test_operador_y_profesional_reciben_403(self):
        for rol in ("operator", "professional"):
            u = get_user_model().objects.create_user(rol, email=f"{rol}@example.com", role=rol)
            self.client.force_login(u)
            for url in self.urls():
                self.assertEqual(self.client.get(url).status_code, 403, (rol, url))
                self.assertEqual(
                    self.client.post(url, {"posicion": 1}).status_code, 403, (rol, url)
                )
        self.assertEqual(Cuenta.objects.get(pk=self.c1.pk).posicion, 1)
