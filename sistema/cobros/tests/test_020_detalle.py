"""020 Fase A: detalle, edición y observaciones (FR-005, FR-006, FR-007, FR-018)."""

from django.urls import reverse

from sistema.arca.tests.fabricas import cobro_facturable, emisor_prueba
from sistema.cobros.models import Cobro
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_permisos_cobros import BasePermisos

MENSAJE_OBS = "Las observaciones pueden tener hasta 1.000 caracteres."


class EditarTest(BasePermisos):
    def test_cancelado_sin_boton_editar_y_la_pantalla_redirige_con_aviso(self):
        c = self.cobro(estado="cancelado")
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(self.url("detalle", c)), self.url("editar", c))
        r = self.client.get(self.url("editar", c), follow=True)
        self.assertRedirects(r, self.url("detalle", c))
        self.assertContains(r, "Un cobro Cancelado solo acepta cambios en observaciones.")


class CancelarConErrorTest(BasePermisos):
    def cancelar(self, **datos):
        c = self.cobro(estado="pendiente")
        self.client.force_login(self.ana)
        return c, self.client.post(self.url("cancelar", c), {"estado_visto": "pendiente", **datos})

    def test_otro_sin_detalle_vuelve_400_con_mas_acciones_abierto_y_foco_en_detalle(self):
        c, r = self.cancelar(motivo="otro", detalle="  ")
        self.assertEqual(r.status_code, 400)
        html = r.content.decode()
        self.assertIn("Escribí el detalle del motivo.", html)
        c.refresh_from_db()
        self.assertEqual(c.estado, "pendiente")

    def test_sin_motivo_vuelve_400_con_foco_en_motivo(self):
        c, r = self.cancelar(motivo="", detalle="x")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "Elegí un motivo de cancelación.", status_code=400)

    def test_otro_con_detalle_cancela(self):
        c, r = self.cancelar(motivo="otro", detalle="x")
        self.assertRedirects(r, self.url("detalle", c))
        c.refresh_from_db()
        self.assertEqual(c.estado, "cancelado")


class ObservacionesLimiteTest(BasePermisos):
    def guardar(self, texto, cobro=None):
        c = cobro or self.cobro(estado="pendiente", observaciones="previa")
        self.client.force_login(self.ana)
        r = self.client.post(self.url("observaciones", c), {"observaciones": texto}, follow=True)
        c.refresh_from_db()
        return c, r

    def test_largos_en_el_limite(self):
        for n, guarda in ((0, True), (999, True), (1000, True), (1001, False)):
            with self.subTest(n=n):
                c, r = self.guardar("a" * n)
                if guarda:
                    self.assertEqual(c.observaciones, "a" * n)
                    self.assertNotContains(r, MENSAJE_OBS)
                else:
                    self.assertEqual(c.observaciones, "previa")
                    self.assertContains(r, MENSAJE_OBS)

    def test_saltos_crlf_cuentan_como_un_caracter(self):
        justo = "\r\n".join(["a" * 90] * 11)  # 990 + 10 saltos = 1000 en el navegador
        c, r = self.guardar(justo)
        self.assertEqual(c.observaciones, justo.replace("\r\n", "\n"))
        self.assertNotContains(r, MENSAJE_OBS)
        c, r = self.guardar(justo + "a")  # 1001
        self.assertEqual(c.observaciones, "previa")
        self.assertContains(r, MENSAJE_OBS)

    def test_adjuntar_con_mas_de_1000_no_guarda_nada(self):
        c = self.cobro(estado="pendiente")
        self.client.force_login(self.ana)
        r = self.client.post(
            self.url("adjuntar", c),
            {"estado_visto": "pendiente", "comprobante": imagen(), "observaciones": "a" * 1001},
            follow=True,
        )
        self.assertContains(r, MENSAJE_OBS)
        c.refresh_from_db()
        self.assertEqual(c.estado, "pendiente")
        self.assertFalse(c.comprobante)

    def test_adjuntar_con_1000_guarda(self):
        c = self.cobro(estado="pendiente")
        self.client.force_login(self.ana)
        self.client.post(
            self.url("adjuntar", c),
            {"estado_visto": "pendiente", "comprobante": imagen(), "observaciones": "a" * 1000},
        )
        c.refresh_from_db()
        self.assertEqual(c.observaciones, "a" * 1000)
        self.assertEqual(c.estado, "finalizado")

    def test_una_observacion_vieja_de_mas_de_1000_se_muestra_entera(self):
        c = self.cobro(estado="pendiente")
        Cobro.objects.filter(pk=c.pk).update(observaciones="b" * 1200)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url("detalle", c)), "b" * 1200)
        r = self.client.get(self.url("editar", c))
        self.assertContains(r, "b" * 1200)
        self.assertContains(r, "data-contador")

    def test_editar_rechaza_una_observacion_nueva_de_1001(self):
        c = self.cobro(estado="pendiente")
        self.client.force_login(self.admin)
        r = self.client.get(self.url("editar", c))
        form = r.context["form"]
        datos = {
            n: (f.value() if f.value() is not None else "")
            for n, f in ((n, form[n]) for n in form.fields)
        }
        datos["observaciones"] = "a" * 1001
        r = self.client.post(self.url("editar", c), datos)
        self.assertContains(r, MENSAJE_OBS)


class SinFacturaVigenteTest(BasePermisos):
    def setUp(self):
        super().setUp()
        dest = self.efectivo.destinatario
        dest.facturacion_activa = True
        dest.emisor = emisor_prueba()
        dest.save()
        cuenta = self.efectivo
        self.facturable = cobro_facturable(cuenta=cuenta, destinatario=dest, creado_por=self.ana)

    def test_el_administrador_ve_la_marca(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("cobros:detalle", args=[self.facturable.pk]))
        self.assertContains(r, "Sin factura vigente")

    def test_el_operador_no_la_ve(self):
        self.client.force_login(self.ana)
        r = self.client.get(reverse("cobros:detalle", args=[self.facturable.pk]))
        self.assertNotContains(r, "Sin factura vigente")
