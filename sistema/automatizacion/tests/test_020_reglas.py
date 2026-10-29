from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from sistema.auditoria.servicios import registrar

from ..models import Regla
from . import ReglasTestCase


class PosicionTest(ReglasTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def datos(self, **extra):
        d = {
            "nombre": "Regla",
            "cuenta": self.jimena.pk,
            "estado": "activa",
            "disparador": "semana",
            "dia_semana": "1",
            "hora": "08:00",
            "accion": "primero",
        }
        d.update(extra)
        return d

    def test_posicion_9_cambiada_a_primero_guarda_vacia_y_resume_la_guardada(self):
        regla = self.regla(cuenta=self.jimena, accion="posicion", posicion=9)
        url = reverse("automatizacion:editar", args=[regla.pk])
        r = self.client.post(url, self.datos(posicion="9"), follow=True)
        self.assertContains(r, "Regla guardada.")
        regla.refresh_from_db()
        self.assertIsNone(regla.posicion)
        r = self.client.get(url)
        self.assertContains(r, "Cada lunes a las 08:00: mover «jimena.roble.rana» a la posición 1.")

    def test_posicion_especifica_sin_numero_sigue_con_error(self):
        r = self.client.post(reverse("automatizacion:nueva"), self.datos(accion="posicion"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "La posición específica debe ser 1 o más.")
        self.assertEqual(Regla.objects.count(), 0)

    def test_posicion_1_vale_y_0_no(self):
        for pos, esperadas in (("1", 1), ("0", 1)):
            self.client.post(
                reverse("automatizacion:nueva"), self.datos(accion="posicion", posicion=pos)
            )
            self.assertEqual(Regla.objects.count(), esperadas)

    def test_otra_accion_ignora_una_posicion_invalida(self):
        self.client.post(reverse("automatizacion:nueva"), self.datos(posicion="-5"))
        self.assertIsNone(Regla.objects.get().posicion)


class UltimaEjecucionTest(ReglasTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def evento(self, regla, hora, **kw):
        with patch("django.utils.timezone.now", return_value=hora):
            registrar("ejecucion_regla", usuario=None, entidad=regla, **kw)

    def test_consultas_iguales_con_1_y_5_reglas(self):
        self.regla(cuenta=self.jimena)
        with CaptureQueriesContext(connection) as uno:
            self.client.get(reverse("automatizacion:lista"))
        for i, c in enumerate([self.tincho, self.rodrigo, self.martin, self.jimena]):
            self.regla(cuenta=c, nombre=f"R{i}", dia=2 + i)
        with CaptureQueriesContext(connection) as cinco:
            self.client.get(reverse("automatizacion:lista"))
        self.assertEqual(len(uno), len(cinco))
