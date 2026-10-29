"""022 Fase A: Nuevo cobro (FR-003, FR-004, FR-007) e inicio del operador (FR-008)."""

from django.urls import reverse

from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_permisos_cobros import BasePermisos

NUEVO = f"{reverse('cobros:nuevo')}?paciente=501"
OPERADOR = reverse("usuarios:operador")


class InicioOperadorTest(BasePermisos):
    def pendientes(self, n):
        for _ in range(n):
            cobro_prueba(
                cuenta=self.cuenta, creado_por=self.ana, destinatario=self.dest, monto="1000.00"
            )
        self.client.force_login(self.ana)
        return self.client.get(OPERADOR)

    def test_con_13_pendientes_la_tarjeta_muestra_13_y_enlaza_a_pendientes(self):
        r = self.pendientes(13)
        self.assertEqual(r.context["pendientes"], 13)
        html = r.content.decode()
        tarjeta = html[html.index("?estado=pendiente") :].split("</a>")[0]
        self.assertIn("Pendientes", tarjeta)
        self.assertRegex(tarjeta, r">\s*13\s*<")
        self.assertNotIn("Pendientes:", html)

    def test_con_cero_pendientes_la_tarjeta_muestra_0(self):
        html = self.pendientes(0).content.decode()
        tarjeta = html[html.index("?estado=pendiente") :].split("</a>")[0]
        self.assertRegex(tarjeta, r">\s*0\s*<")
        self.assertEqual(Cobro.objects.count(), 0)
