"""020 Fase 0: nombre de cuenta, mensajes de monto, archivos por X-Accel y factura vigente."""

from decimal import Decimal as D

from django import forms
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from django.urls import reverse

from sistema.arca.servicios import sin_factura_vigente
from sistema.arca.tests.fabricas import cobro_facturable, emisor_prueba, factura_prueba
from sistema.cobros.models import ComprobanteCobro
from sistema.cobros.tests.fabrica import pago_prueba
from sistema.cobros.tests.test_permisos_cobros import BasePermisos
from sistema.core.forms import MontoDecimalField
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

CBU = "0000000000000000000001"


class CuentaNombreTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.dest = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo 3", tipo="profesional"
        )

    def transferencia(self, **kw):
        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            tope_mensual=D("1000.00"),
            **kw,
        )

    def test_transferencia_sin_alias_usa_ultimos_cuatro_del_cbu(self):
        c = self.transferencia(cbu=CBU)
        self.assertEqual(str(c), "Destinatario Ejemplo 3 — CBU terminado en 0001")
        self.assertNotIn(CBU, str(c))


class F(forms.Form):
    monto = MontoDecimalField(max_digits=12, decimal_places=2, min_value=D("0.00"))


class MontoMensajesTest(SistemaTestCase):
    def error(self, texto):
        f = F({"monto": texto})
        self.assertFalse(f.is_valid(), texto)
        return f.errors["monto"][0]

    def test_cuatro_textos_de_fr_030(self):
        self.assertEqual(self.error("abc"), "Escribí un monto válido, por ejemplo 1.500,50.")
        self.assertEqual(
            self.error("12345678901,00"), "El monto puede tener hasta 10 dígitos antes de la coma."
        )
        self.assertEqual(self.error("10,999"), "Escribí hasta 2 decimales.")
        self.assertEqual(self.error("-0,01"), "El monto no puede ser menor que $0,00.")

    def test_limite_de_digitos_y_cero(self):
        for texto, esperado in (
            ("9999999999,99", D("9999999999.99")),
            ("0", D("0")),
            ("0,01", D("0.01")),
            ("1.500", D("1500.00")),
            ("40.000,50", D("40000.50")),
        ):
            with self.subTest(texto=texto):
                f = F({"monto": texto})
                self.assertTrue(f.is_valid(), f.errors)
                self.assertEqual(f.cleaned_data["monto"], esperado)

    def test_modelform_sin_campo_declarado_usa_monto_decimal_field(self):
        class CuentaForm(forms.ModelForm):
            class Meta:
                model = Cuenta
                fields = ["tope_mensual"]

        campo = CuentaForm().fields["tope_mensual"]
        self.assertIsInstance(campo, MontoDecimalField)
        f = CuentaForm({"tope_mensual": "10,999"})
        self.assertFalse(f.is_valid())
        self.assertEqual(f.errors["tope_mensual"], ["Escribí hasta 2 decimales."])


class ArchivosTest(BasePermisos):
    ACCEL = {"HTTP_X_SENDFILE_TYPE": "X-Accel-Redirect"}

    def setUp(self):
        super().setUp()
        self.nombre = default_storage.save(
            "comprobantes/2026/10/ejemplo.jpg", ContentFile(b"datos")
        )
        self.cobro_ = self.cobro(estado="finalizado", comprobante=self.nombre)
        self.otro = ComprobanteCobro.objects.create(
            cobro=self.cobro_, archivo=self.nombre, creado_por=self.ana
        )
        self.pago = pago_prueba(
            destinatario=self.dest,
            cuenta=self.cuenta,
            monto="1000.00",
            comprobante=self.nombre,
            creado_por=self.admin,
        )

    def urls(self):
        return [
            reverse("cobros:comprobante", args=[self.cobro_.pk]),
            reverse("cobros:comprobante_otro", args=[self.otro.pk]),
            reverse("cobros:complementario_comprobante", args=[self.pago.pk]),
        ]

    def test_administrador_recibe_el_archivo_desde_django(self):
        self.client.force_login(self.admin)
        for url in self.urls():
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(b"".join(r.streaming_content), b"datos")
                self.assertNotIn("X-Accel-Redirect", r)

    def test_con_x_sendfile_type_responde_vacio_y_x_accel_redirect(self):
        self.client.force_login(self.admin)
        for url in self.urls():
            with self.subTest(url=url):
                r = self.client.get(url, **self.ACCEL)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.content, b"")
                self.assertEqual(r["X-Accel-Redirect"], "/media/" + self.nombre)
                self.assertEqual(r["Content-Type"], "image/jpeg")

    def test_operador_y_profesional_reciben_403_aun_con_el_encabezado(self):
        for usuario in (self.luis, self.drax):
            self.client.force_login(usuario)
            for url in self.urls():
                with self.subTest(usuario=usuario.username, url=url):
                    r = self.client.get(url, **self.ACCEL)
                    # 021/FR-012: el operador ajeno al cobro recibe 404 en el comprobante 1
                    propio = usuario is self.luis and url == self.urls()[0]
                    self.assertEqual(r.status_code, 404 if propio else 403)
                    self.assertNotIn("X-Accel-Redirect", r)

    def test_sin_sesion_va_al_login(self):
        for url in self.urls():
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r["Location"].startswith(reverse("usuarios:login")))

    def test_media_no_la_sirve_django(self):
        for debug in (False, True):
            with self.subTest(debug=debug), override_settings(DEBUG=debug):
                r = self.client.get("/media/" + self.nombre)
                self.assertEqual(r.status_code, 404)


class SinFacturaVigenteTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()
        self.dest = Destinatario.objects.create(
            nombre="Clínica Ejemplo", tipo="clinica", facturacion_activa=True, emisor=self.emisor
        )
        self.cuenta = Cuenta.objects.create(destinatario=self.dest, tipo="efectivo")

    def cobro(self, **kw):
        return cobro_facturable(cuenta=self.cuenta, destinatario=self.dest, **kw)

    def test_finalizado_facturable_sin_factura_es_true(self):
        self.assertTrue(sin_factura_vigente(self.cobro()))

    def test_con_una_vigente_es_false(self):
        c = self.cobro()
        factura_prueba(c, emisor=self.emisor, vigente=True)
        self.assertFalse(sin_factura_vigente(c))

    def test_con_solo_anuladas_es_true(self):
        c = self.cobro()
        factura_prueba(c, emisor=self.emisor, vigente=False)
        self.assertTrue(sin_factura_vigente(c))

    def test_pendiente_es_false(self):
        self.assertFalse(
            sin_factura_vigente(self.cobro(estado="pendiente", metodo="transferencia"))
        )

    def test_no_facturable_es_false(self):
        Destinatario.objects.filter(pk=self.dest.pk).update(facturacion_activa=False, emisor=None)
        c = self.cobro()
        c.refresh_from_db()
        self.assertFalse(sin_factura_vigente(c))
