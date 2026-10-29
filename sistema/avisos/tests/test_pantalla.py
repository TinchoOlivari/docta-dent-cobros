from django.contrib.messages import get_messages
from django.urls import reverse

from payment_instructions.models import User
from sistema.avisos.models import DireccionAviso
from sistema.avisos.servicios import agregar_direccion, margen
from sistema.avisos.tests import AvisosTestCase


class PantallaTests(AvisosTestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_user(
            "admin", email="admin@example.com", password="x", role="administrator"
        )
        User.objects.create_user("ana", email="ana@example.com", password="x", role="operator")
        self.client.login(username="admin", password="x")

    def mensajes(self, r):
        return [str(m) for m in get_messages(r.wsgi_request)]

    def test_agregar_direccion_valida_la_lista(self):
        r = self.client.post(reverse("avisos:agregar"), {"email": "duenio@clinica.test"})
        self.assertRedirects(r, reverse("avisos:configuracion"))
        self.assertTrue(DireccionAviso.objects.filter(email="duenio@clinica.test").exists())

    def test_agregar_direccion_invalida_muestra_error_y_no_guarda(self):
        r = self.client.post(reverse("avisos:agregar"), {"email": "duenio@clinica"}, follow=True)
        self.assertEqual(DireccionAviso.objects.count(), 0)

    def test_margen_20_se_guarda(self):
        r = self.client.post(reverse("avisos:configuracion"), {"margen": "20"}, follow=True)
        self.assertEqual(margen(), 20)

    def test_margen_100_muestra_error_y_no_cambia(self):
        r = self.client.post(reverse("avisos:configuracion"), {"margen": "100"})
        self.assertEqual(margen(), 10)

    def test_borrar_saca_la_direccion(self):
        d = agregar_direccion(usuario=None, email="duenio@clinica.test")
        r = self.client.post(reverse("avisos:borrar", args=[d.pk]), follow=True)
        self.assertEqual(DireccionAviso.objects.count(), 0)

    def test_agregar_y_borrar_por_get_dan_405(self):
        d = agregar_direccion(usuario=None, email="duenio@clinica.test")
        self.assertEqual(self.client.get(reverse("avisos:agregar")).status_code, 405)
        self.assertEqual(self.client.get(reverse("avisos:borrar", args=[d.pk])).status_code, 405)
