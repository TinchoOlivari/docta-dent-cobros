"""022 fase E: avisos de lote, barra de elegidos y formulario de reglas (FR-001, FR-022, FR-027)."""

from django.contrib.messages import get_messages
from django.urls import reverse

from ..models import Regla
from . import ReglasTestCase

ESTADO = reverse("automatizacion:estado")


class ReglasLoteTest(ReglasTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def mensajes(self, r):
        return [(m.level_tag, str(m)) for m in get_messages(r.wsgi_request)]

    def test_una_regla_inactiva_se_activa_en_singular(self):
        a = self.regla(cuenta=self.rodrigo, estado="inactiva")
        r = self.client.post(ESTADO, {"pks": [a.pk], "estado": "activa"})
        self.assertEqual(self.mensajes(r), [("success", "1 regla activada.")])

    def test_tres_reglas_se_desactivan_en_plural(self):
        reglas = [
            self.regla(cuenta=c, nombre=str(i))
            for i, c in enumerate((self.rodrigo, self.jimena, self.martin))
        ]
        r = self.client.post(ESTADO, {"pks": [x.pk for x in reglas], "estado": "inactiva"})
        self.assertEqual(self.mensajes(r), [("success", "3 reglas desactivadas.")])

    def test_cero_reglas_da_error_y_no_cambia_nada(self):
        self.regla(cuenta=self.rodrigo)
        r = self.client.post(ESTADO, {"estado": "inactiva"})
        self.assertEqual(self.mensajes(r), [("error", "Elegí al menos una regla.")])
        self.assertEqual(Regla.objects.filter(estado="inactiva").count(), 0)

    def test_pk_no_numerico_da_error_y_no_500(self):
        r = self.client.post(ESTADO, {"pks": ["x"], "estado": "inactiva"})
        self.assertEqual(self.mensajes(r), [("error", "Elegí al menos una regla.")])
