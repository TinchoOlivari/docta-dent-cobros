from django.contrib.auth import get_user_model
from django.urls import reverse

from ..models import Regla
from . import ReglasTestCase


class PermisosTest(ReglasTestCase):
    def setUp(self):
        super().setUp()
        self.regla_existente = self.regla(cuenta=self.rodrigo)
        User = get_user_model()
        self.ana = User.objects.create_user("ana", email="ana@example.com", role="operator")
        self.drax = User.objects.create_user("drax", email="drax@example.com", role="professional")

    def urls(self):
        return [
            reverse("automatizacion:lista"),
            reverse("automatizacion:nueva"),
            reverse("automatizacion:editar", args=[self.regla_existente.pk]),
            reverse("automatizacion:estado"),
        ]

    def datos(self):
        return {
            "nombre": "Intruso",
            "cuenta": self.jimena.pk,
            "estado": "activa",
            "disparador": "semana",
            "dia_semana": "2",
            "hora": "09:00",
            "accion": "primero",
            "pks": [self.regla_existente.pk],
        }

    def test_sin_sesion_redirige_al_login(self):
        for url in self.urls():
            for metodo in (self.client.get, self.client.post):
                r = metodo(url, self.datos() if metodo == self.client.post else None)
                self.assertEqual(r.status_code, 302, url)
                self.assertIn(reverse("usuarios:login"), r["Location"])

    def test_operador_y_profesional_reciben_403_y_nada_cambia(self):
        for usuario in (self.ana, self.drax):
            self.client.force_login(usuario)
            for url in self.urls():
                self.assertEqual(self.client.get(url).status_code, 403, url)
                datos = self.datos() | {"estado": "inactiva"}
                self.assertEqual(self.client.post(url, datos).status_code, 403, url)
        self.assertEqual(Regla.objects.count(), 1)
        self.regla_existente.refresh_from_db()
        self.assertEqual(self.regla_existente.estado, "activa")

    def test_administrador_ve_la_lista(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("automatizacion:lista")).status_code, 200)
