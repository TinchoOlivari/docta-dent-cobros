"""Flujo crítico: ingreso con usuario y contraseña; el operador no entra a pantallas de administrador."""

from sistema.core.e2e.base import CLAVE, E2ECase
from sistema.usuarios import roles
from payment_instructions.models import User


class FlujoAccesoE2E(E2ECase):
    def ingresar(self, rol):
        usuario = User.objects.create_user(
            f"flujo-{rol}", email=f"{rol}@example.com", password=CLAVE, role=rol
        )
        p = self.nueva_pagina()
        p.goto(self.url("/sistema/login/"))
        p.fill("input[name=username]", usuario.username)
        p.fill("input[name=password]", CLAVE)
        p.get_by_role("button", name="Ingresar").click()
        p.wait_for_url(lambda url: "/login/" not in url)
        return p

    def test_administrador_ingresa_y_ve_las_terminales(self):
        p = self.ingresar(roles.ADMIN)
        self.assertIn("/panel/", p.url)
        respuesta = p.goto(self.url("/sistema/terminales/"))
        self.assertEqual(respuesta.status, 200)

    def test_operador_ingresa_y_recibe_403_en_pantallas_de_administrador(self):
        p = self.ingresar(roles.OPERATOR)
        self.assertIn("/operador/", p.url)
        for ruta in ("/sistema/terminales/", "/sistema/arca/emisores/", "/sistema/panel/"):
            self.assertEqual(p.goto(self.url(ruta)).status, 403, ruta)
