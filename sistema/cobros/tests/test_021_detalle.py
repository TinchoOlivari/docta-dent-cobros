"""021 Fase A: detalle del cobro y comprobantes (FR-005 a FR-014, FR-040)."""

from django.core.files.uploadedfile import SimpleUploadedFile

from sistema.arca.tests.fabricas import AHORA as AHORA_ARCA
from sistema.arca.tests.fabricas import factura_prueba
from sistema.cobros import comprobante
from sistema.cobros.models import Cobro
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_permisos_cobros import BasePermisos

CANCELADO = (
    "Un administrador canceló este cobro. No transfieras. "
    "Si ya transferiste, avisá a un administrador."
)
FINALIZADO = "El cobro ya está Finalizado. No hace falta otro comprobante."
VIEJO = "Revisá los datos y volvé a intentar"
RECARGAR = "Recargá la página para ver el estado actual."
DIAS_15 = 15 * 24 * 60


class ConcurrenciaTest(BasePermisos):
    """FR-007."""

    def adjuntar(self, cobro, **datos):
        self.client.force_login(self.ana)
        return self.client.post(
            self.url("adjuntar", cobro),
            {"comprobante": imagen(), "estado_visto": "pendiente", **datos},
            follow=True,
        )

    def test_cancelado_dice_que_no_transfiera(self):
        r = self.adjuntar(self.cobro(estado="cancelado"))
        self.assertContains(r, CANCELADO)
        self.assertNotContains(r, VIEJO)

    def test_finalizado_dice_que_no_hace_falta_otro_comprobante(self):
        r = self.adjuntar(self.cobro(estado="finalizado"))
        self.assertContains(r, FINALIZADO)
        self.assertNotContains(r, VIEJO)

    def test_estado_faltante_dice_que_recargue(self):
        c = self.cobro()
        self.client.force_login(self.ana)
        r = self.client.post(
            self.url("adjuntar", c), {"comprobante": imagen(), "estado_visto": ""}, follow=True
        )
        self.assertContains(r, RECARGAR)
        self.assertNotContains(r, "Recargá la página y volvé a intentar")
        c.refresh_from_db()
        self.assertEqual(c.estado, "pendiente")

    def test_mismo_estado_adjunta(self):
        c = self.cobro()
        self.adjuntar(c)
        c.refresh_from_db()
        self.assertEqual(c.estado, "finalizado")


class CuentaYCopiarTest(BasePermisos):
    """FR-008, FR-009."""

    def test_operador_no_ve_cuenta_ni_copiar_en_finalizado_ni_cancelado(self):
        self.client.force_login(self.ana)
        for estado in ("finalizado", "cancelado"):
            with self.subTest(estado=estado):
                html = self.client.get(self.url("detalle", self.cobro(estado=estado))).content
                html = html.decode()
                self.assertEqual(html.count("Copiar"), 0)
                self.assertNotIn("Cuenta destino", html)
                self.assertNotIn("prueba.uno", html)

    def test_administrador_ve_la_cuenta_en_finalizado(self):
        self.client.force_login(self.admin)
        r = self.client.get(self.url("detalle", self.cobro(estado="finalizado")))
        self.assertContains(r, "prueba.uno")


class AbrirComprobanteTest(BasePermisos):
    """FR-012 (D-15): casos límite de la ventana de 15 días."""

    def con_comprobante(self, **kw):
        from sistema.cobros import servicios

        c = self.cobro(**kw)
        servicios.adjuntar_comprobante(c, imagen(), usuario=self.ana)
        return c

    def codigo(self, usuario, cobro):
        return self.como(usuario).get(self.url("comprobante", cobro)).status_code

    def test_operador_abre_el_propio_de_hoy(self):
        self.assertEqual(self.codigo(self.ana, self.con_comprobante()), 200)

    def test_operador_no_abre_el_de_otro_operador(self):
        self.assertEqual(self.codigo(self.ana, self.con_comprobante(creador=self.luis)), 404)

    def test_operador_abre_el_de_justo_15_dias(self):
        self.assertEqual(self.codigo(self.ana, self.con_comprobante(minutos_atras=DIAS_15)), 200)

    def test_operador_no_abre_el_de_15_dias_y_un_minuto(self):
        self.assertEqual(
            self.codigo(self.ana, self.con_comprobante(minutos_atras=DIAS_15 + 1)), 404
        )

    def test_operador_no_abre_el_de_16_dias(self):
        self.assertEqual(
            self.codigo(self.ana, self.con_comprobante(minutos_atras=16 * 24 * 60)), 404
        )

    def test_profesional_recibe_403(self):
        self.assertEqual(self.codigo(self.drax, self.con_comprobante()), 403)

    def test_administrador_abre_cualquiera(self):
        c = self.con_comprobante(creador=self.luis, minutos_atras=16 * 24 * 60)
        self.assertEqual(self.codigo(self.admin, c), 200)


class ArchivoDanadoTest(BasePermisos):
    """FR-013."""

    def png_cortado(self):
        return SimpleUploadedFile("c.png", imagen().read()[:-20])

    def test_png_con_firma_valida_y_datos_cortados_da_el_error_sin_500(self):
        c = self.cobro()
        self.client.force_login(self.ana)
        r = self.client.post(
            self.url("adjuntar", c),
            {"comprobante": self.png_cortado(), "estado_visto": "pendiente"},
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No se pudo leer el archivo. Probá con otra foto o con un PDF.")
        c.refresh_from_db()
        self.assertEqual(c.estado, "pendiente")
        self.assertFalse(c.comprobante)
        self.assertEqual(c.otros_comprobantes.count(), 0)

    def test_firma_png_con_datos_basura_no_lanza(self):
        archivo = SimpleUploadedFile("c.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        with self.assertRaises(Exception) as ctx:
            comprobante.validar_y_comprimir(archivo)
        self.assertEqual(type(ctx.exception).__name__, "ValidationError")


class NotaDeCreditoObligatoriaTest(BasePermisos):
    """FR-040."""

    def setUp(self):
        super().setUp()
        self.c = self.cobro(estado="finalizado", destinatario=self.dest)
        factura_prueba(
            self.c,
            estado="emitido",
            numero=1,
            punto_venta=3,
            cae="75123456789012",
            cae_vence=AHORA_ARCA.date(),
            fecha=AHORA_ARCA.date(),
            total="7100.00",
        )
        self.client.force_login(self.admin)

    def cancelar(self, **datos):
        return self.client.post(
            self.url("cancelar", self.c),
            {"estado_visto": "finalizado", "motivo": "otro_medio", **datos},
        )

    def test_sin_elegir_da_el_error_y_el_cobro_sigue(self):
        r = self.cancelar()
        self.assertContains(r, "Elegí Sí o No.", status_code=400)
        self.c.refresh_from_db()
        self.assertEqual(self.c.estado, "finalizado")

    def test_el_dialogo_repite_la_pregunta_obligatoria(self):
        r = self.client.get(self.url("detalle", self.c))
        self.assertContains(r, "¿Emitir nota de crédito por $7.100,00?")
        self.assertRegex(r.content.decode(), r'name="nota_credito" value="si" required')

    def test_con_no_cancela(self):
        self.cancelar(nota_credito="no")
        self.c.refresh_from_db()
        self.assertEqual(self.c.estado, "cancelado")

    def test_cobro_sin_factura_no_pregunta(self):
        c = self.cobro(estado="pendiente")
        r = self.client.post(
            self.url("cancelar", c), {"estado_visto": "pendiente", "motivo": "otro_medio"}
        )
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.estado, Cobro.Estado.CANCELADO)
