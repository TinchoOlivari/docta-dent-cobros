"""002/FR-009 y 002/SC-001: una acción que no está en la tabla es solo del administrador."""

from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.urls import resolve, reverse

from sistema.arca.models import ComprobanteFiscal, Emisor
from sistema.arca.tests.fabricas import cobro_facturable, emisor_prueba, factura_prueba
from sistema.arca.tests.test_pdf import EMITIDA, FacturaBase
from sistema.cobros import servicios
from sistema.cobros.models import Cobro

from sistema.destinatarios.models import Cuenta, Destinatario

D = Decimal
GET, POST = "get", "post"


class PermisosArcaTest(FacturaBase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()
        self.factura = factura_prueba(
            cobro_facturable(creado_por=self.operador), emisor=self.emisor, **EMITIDA
        )
        self.cobro = self.factura.cobro

    def pantallas(self):
        """Cada URL de sistema/arca con su método y datos mínimos: la tabla del contrato."""
        c, f, e = self.cobro.pk, self.factura.pk, self.emisor.pk
        return [
            (GET, reverse("arca:emisores"), {}),
            (GET, reverse("arca:nuevo_emisor"), {}),
            (POST, reverse("arca:nuevo_emisor"), {}),
            (GET, reverse("arca:emisor", args=[e]), {}),
            (POST, reverse("arca:emisor", args=[e]), {}),
            (POST, reverse("arca:estado_emisor", args=[e]), {"estado": "inactivo"}),
            (GET, reverse("arca:datos_factura", args=[c]), {}),
            (POST, reverse("arca:datos_factura", args=[c]), {}),
            (POST, reverse("arca:nota_credito", args=[c]), {}),
            (POST, reverse("arca:revisar", args=[c]), {"emitir": "si"}),
            (GET, reverse("arca:pdf", args=[f]), {}),
            (POST, reverse("arca:reintentar", args=[f]), {}),
            (GET, reverse("arca:libro"), {}),
            (GET, reverse("arca:libro_descargar"), {"formato": "csv"}),
        ]

    def pedir(self, metodo, url, datos):
        return getattr(self.client, metodo)(url, datos)

    def test_la_tabla_cubre_todas_las_urls_de_arca(self):
        from sistema.arca.urls import urlpatterns

        for patron in urlpatterns:
            with self.subTest(url=patron.name):
                self.assertTrue(
                    any(f"arca:{patron.name}" == self.nombre(url) for _, url, _ in self.pantallas())
                )

    @staticmethod
    def nombre(url):
        return resolve(url).view_name

    def test_sin_sesion_redirige_al_login_y_no_cambia_nada(self):
        for metodo, url, datos in self.pantallas():
            with self.subTest(metodo=metodo, url=url):
                r = self.pedir(metodo, url, datos)
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r["Location"].startswith(reverse("usuarios:login")))
        self.sin_efectos()

    def test_operador_y_profesional_reciben_403_y_no_cambian_nada(self):
        for usuario in (self.operador, self.profesional):
            self.client.force_login(usuario)
            for metodo, url, datos in self.pantallas():
                with self.subTest(rol=usuario.role, metodo=metodo, url=url):
                    self.assertEqual(self.pedir(metodo, url, datos).status_code, 403)
        self.sin_efectos()

    def test_administrador_entra_a_todas_las_urls(self):
        self.client.force_login(self.admin)
        for metodo, url, datos in self.pantallas():
            with self.subTest(metodo=metodo, url=url):
                self.assertIn(self.pedir(metodo, url, datos).status_code, (200, 302))

    def sin_efectos(self):
        self.emisor.refresh_from_db()
        self.assertEqual(self.emisor.estado, "activo")
        self.assertEqual(Emisor.objects.count(), 1)
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)
        self.factura.refresh_from_db()
        self.assertEqual((self.factura.estado, self.factura.vigente), ("emitido", True))


class OperadorYFacturaTest(FacturaBase):
    def test_operador_cancela_su_cobro_pendiente_sin_pregunta_ni_seccion_factura(self):
        destinatario = Destinatario.objects.create(nombre="Clínica", tipo="clinica")
        cuenta = Cuenta.objects.create(
            destinatario=destinatario,
            tipo="transferencia",
            alias="alias.uno",
            tope_mensual=D("1000000.00"),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )
        cobro = cobro_facturable(
            creado_por=self.operador, estado="pendiente", metodo="transferencia", cuenta=cuenta
        )
        self.client.force_login(self.operador)
        detalle = self.client.get(reverse("cobros:detalle", args=[cobro.pk]))
        self.assertEqual(detalle.status_code, 200)
        self.assertNotContains(detalle, 'id="factura"')
        self.assertNotContains(detalle, "pregunta-nota-credito")
        r = self.client.post(
            reverse("cobros:cancelar", args=[cobro.pk]),
            {"motivo": "otro_medio", "estado_visto": "pendiente"},
        )
        self.assertEqual(r.status_code, 302)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "cancelado")
        self.assertEqual(ComprobanteFiscal.objects.count(), 0)

    def test_operador_no_ve_la_factura_de_su_cobro_finalizado_ni_puede_cancelarlo(self):
        factura = factura_prueba(cobro_facturable(creado_por=self.operador), **EMITIDA)
        self.client.force_login(self.operador)
        detalle = self.client.get(reverse("cobros:detalle", args=[factura.cobro_id]))
        self.assertEqual(detalle.status_code, 200)
        for texto in ('id="factura"', "pregunta-nota-credito", "Cancelar el cobro", EMITIDA["cae"]):
            self.assertNotContains(detalle, texto)
        r = self.client.post(
            reverse("cobros:cancelar", args=[factura.cobro_id]),
            {"motivo": "otro_medio", "estado_visto": "finalizado", "nota_credito": "si"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Cobro.objects.get(pk=factura.cobro_id).estado, "finalizado")
        self.assertEqual(ComprobanteFiscal.objects.filter(tipo="nota_credito").count(), 0)

    def test_editar_cobro_con_nota_de_credito_de_un_operador_sigue_en_permission_denied(self):
        cobro = cobro_facturable(creado_por=self.operador)
        for valor in (True, False, None):
            with self.subTest(nota_credito=valor), self.assertRaises(PermissionDenied):
                servicios.editar_cobro(
                    cobro, usuario=self.operador, estado_visto="finalizado", nota_credito=valor
                )

    def test_editar_cobro_por_http_de_un_operador_da_403(self):
        cobro = cobro_facturable(creado_por=self.operador)
        self.client.force_login(self.operador)
        r = self.client.post(
            reverse("cobros:editar", args=[cobro.pk]),
            {"estado_visto": "finalizado", "nota_credito": "si"},
        )
        self.assertEqual(r.status_code, 403)
