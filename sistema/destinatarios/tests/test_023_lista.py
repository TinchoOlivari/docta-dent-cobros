from django.contrib.auth import get_user_model

from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Destinatario
from sistema.destinatarios.tests import test_017_lote


class ArchivarLoteTest(test_017_lote.LoteHelpers, SistemaTestCase):
    """023/FR-027 a FR-029. Reusa los ayudantes de LoteHelpers."""

    def test_archivar_2_activos_y_1_inactivo(self):
        a, b = self.dest("Aaa"), self.dest("Bbb")
        c = self.dest("Ccc", "inactivo")
        antes = Evento.objects.filter(entidad="destinatarios.destinatario").count()
        r = self.client.post(
            self.url, {"pk": [a.pk, b.pk, c.pk], "estado": "archivado"}, follow=True
        )
        self.assertIn("Cambiados: 3. Omitidos: 0.", [str(m) for m in r.context["messages"]])
        estados = set(Destinatario.objects.values_list("estado", flat=True))
        self.assertEqual(estados, {"archivado"})
        despues = Evento.objects.filter(entidad="destinatarios.destinatario").count()
        self.assertGreaterEqual(despues - antes, 3)

    def test_archivar_con_sesion_pos_abierta_se_omite(self):
        a, d = self.dest("Aaa"), self.dest("Ddd")
        self.con_sesion_abierta(d)
        r = self.client.post(self.url, {"pk": [a.pk, d.pk], "estado": "archivado"}, follow=True)
        self.assertIn("Cambiados: 1. Omitidos: 1.", [str(m) for m in r.context["messages"]])
        self.assertEqual(Destinatario.objects.get(pk=d.pk).estado, "activo")

    def test_estado_borrado_es_400_sin_cambios(self):
        a = self.dest("Aaa")
        r = self.client.post(self.url, {"pk": [a.pk], "estado": "borrado"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.content.decode(), "Estado inválido.")
        self.assertEqual(Destinatario.objects.get(pk=a.pk).estado, "activo")

    def test_operador_no_archiva(self):
        a = self.dest("Aaa")
        op = get_user_model().objects.create_user("o23", email="o23@example.com", role="operator")
        self.client.force_login(op)
        r = self.client.post(self.url, {"pk": [a.pk], "estado": "archivado"})
        self.assertEqual(r.status_code, 403)
