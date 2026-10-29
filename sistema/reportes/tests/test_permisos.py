from django.test import SimpleTestCase
from django.contrib.messages import get_messages
from django.urls import reverse

from payment_instructions.models import User
from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_destinatario, editar_destinatario

from . import DatosMartinO


class PermisosReporteTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.operador = User.objects.create_user("ana", email="ana@example.com", role="operator")
        self.drax = User.objects.create_user("drax", email="drax@example.com", role="professional")
        self.otro = User.objects.create_user("otro", email="otro@example.com", role="professional")
        editar_destinatario(self.martin, usuario=self.admin, usuario_profesional=self.drax)
        self.jimena = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        self.rep = reverse("reportes:destinatario", args=[self.martin.pk])
        self.rep_jimena = reverse("reportes:destinatario", args=[self.jimena.pk])
        self.desc = reverse("reportes:destinatarios_descargar") + "?formato=csv"

    def status(self, usuario, url):
        if usuario:
            self.client.force_login(usuario)
        return self.client.get(url).status_code

    def test_reporte_sin_sesion_redirige_al_login(self):
        r = self.client.get(self.rep)
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("usuarios:login"), r["Location"])

    def test_reporte_por_rol(self):
        self.assertEqual(self.status(self.admin, self.rep), 200)
        self.assertEqual(self.status(self.operador, self.rep), 403)

    def test_profesional_ve_el_suyo_y_otro_da_404(self):
        self.assertEqual(self.status(self.drax, self.rep), 200)
        self.assertEqual(self.status(None, self.rep_jimena), 404)

    def test_profesional_sin_vinculo_da_404(self):
        self.assertEqual(self.status(self.otro, self.rep), 404)

    def test_descarga_de_destinatarios_solo_admin(self):
        self.assertEqual(self.status(self.admin, self.desc), 200)
        self.assertEqual(self.status(self.operador, self.desc), 403)
        self.assertEqual(self.status(self.drax, self.desc), 403)

    def test_ningun_get_crea_evento(self):
        for u in (self.admin, self.operador, self.drax):
            self.client.force_login(u)  # el login mismo registra un evento
            antes = Evento.objects.count()
            self.client.get(self.rep)
            self.client.get(self.desc)
            self.assertEqual(Evento.objects.count(), antes)


class PermisosElegidosTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("reportes:cobros_elegidos")
        self.c = self.cobro(self.tincho, "100.00")

    def test_operador_y_profesional_reciben_403_en_las_3_acciones(self):
        op = User.objects.create_user("ana", email="ana@example.com", role="operator")
        pro = User.objects.create_user("pro", email="pro@example.com", role="professional")
        for u in (op, pro):
            self.client.force_login(u)
            for accion in ("descargar", "comprobantes", "copiar"):
                r = self.client.post(
                    self.url,
                    {"pk": [self.c.pk], "accion": accion, "formato": "csv", "por": "cobro"},
                )
                self.assertEqual(r.status_code, 403)

    def test_get_como_admin_da_405(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_sin_pk_o_pk_inexistente_avisa_y_no_crea_evento(self):
        self.client.force_login(self.admin)
        antes = Evento.objects.count()
        for datos in ({"accion": "copiar"}, {"pk": [999999], "accion": "copiar"}):
            r = self.client.post(self.url, datos)
            self.assertEqual(r.status_code, 302)
            self.assertIn(
                "Elegí al menos un cobro.", [str(m) for m in get_messages(r.wsgi_request)]
            )
        self.client.post(self.url, {"pk": [self.c.pk], "accion": "copiar"})
        self.assertEqual(Evento.objects.count(), antes)


class FueraDeLaPrimeraVersionTest(SimpleTestCase):
    def test_no_hay_pantallas_de_estadisticas_ni_caja_diaria(self):
        # FR-020
        from sistema.reportes.urls import urlpatterns

        for p in urlpatterns:
            for palabra in ("estadistica", "caja", "profesional"):
                self.assertNotIn(palabra, str(p.pattern))
                self.assertNotIn(palabra, p.name)
