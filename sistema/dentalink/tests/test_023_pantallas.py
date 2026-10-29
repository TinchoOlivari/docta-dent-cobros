"""023 Fase E: datos del paciente, ayuda A1 y título del tratamiento (FR-032 a FR-034)."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse

from sistema.core.tests import SistemaTestCase
from sistema.dentalink.tests.test_pantallas import OP, crear_usuarios

SERVICIO = "sistema.dentalink.servicios.cliente"
JUANA = {"id": 501, "nombre": "Juana Ejemplo Prueba", "dni": "30111222"}
NAC = date(1980, 3, 5)


def _tratamiento(nombre="pruebaaaa"):
    return {
        "id": 16423,
        "nombre": nombre,
        "id_profesional": 12,
        "profesionales": [{"id": 12, "nombre": "Dr. Y"}],
        "prestaciones": [
            {
                "id": 9001,
                "id_prestacion": 9001,
                "nombre": "Brackets",
                "id_profesional": None,
                "total": Decimal("100.00"),
                "pagado": Decimal("0.00"),
            }
        ],
    }


class PantallasDentalinkTest(SistemaTestCase):
    def setUp(self):
        crear_usuarios()
        self.client.login(username=OP, password="x")

    @patch(SERVICIO)
    def _buscar(self, paciente, cliente):
        cliente.buscar_pacientes.return_value = [paciente]
        return self.client.get(reverse("dentalink:pacientes"), {"q": "Juana"})

    @patch(SERVICIO)
    def _detalle(self, cliente, tratamiento=None, paciente=None):
        cliente.tratamientos.return_value = [tratamiento or _tratamiento()]
        cliente.paciente.return_value = paciente or {**JUANA, "nacimiento": NAC}
        return self.client.get(reverse("dentalink:paciente", args=[501]))

    def test_fila_con_dni_y_nacimiento(self):
        r = self._buscar({**JUANA, "nacimiento": NAC})
        self.assertContains(r, "Juana Ejemplo Prueba")
        self.assertContains(r, "DNI 30111222 · Nac. 05/03/1980")

    def test_fila_sin_dni(self):
        r = self._buscar({**JUANA, "dni": "", "nacimiento": NAC})
        self.assertContains(r, "Sin DNI · Nac. 05/03/1980")

    def test_fila_sin_nacimiento(self):
        r = self._buscar({**JUANA, "nacimiento": None})
        self.assertContains(r, "DNI 30111222")
        self.assertNotContains(r, "Nac.")

    @patch(SERVICIO)
    def test_el_paciente_se_pide_una_sola_vez(self, cliente):
        cliente.tratamientos.return_value = [_tratamiento()]
        cliente.paciente.return_value = {**JUANA, "nacimiento": NAC}
        self.client.get(reverse("dentalink:paciente", args=[501]))
        cliente.paciente.assert_called_once_with(501)

    def test_tratamiento_con_id_y_nombre(self):
        self.assertContains(self._detalle(), "#16423: pruebaaaa")

    def test_subtitulo_con_nombre_dni_y_nacimiento(self):
        r = self._detalle()
        self.assertContains(r, "Juana Ejemplo Prueba")
        self.assertContains(r, "DNI 30111222 · Nac. 05/03/1980")

    def test_subtitulo_sin_dni_ni_nacimiento(self):
        r = self._detalle(paciente={**JUANA, "dni": "", "nacimiento": None})
        self.assertContains(r, "Sin DNI")
        self.assertNotContains(r, "Nac.")
