from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError

from sistema.arca.models import ComprobanteFiscal, Emisor
from sistema.arca.tests.fabricas import (
    AHORA,
    cobro_facturable,
    emisor_prueba,
    factura_prueba,
)
from sistema.core.tests import SistemaTestCase

D = Decimal


class EmisorTest(SistemaTestCase):
    def falla(self, nombre, **campos):
        with self.assertRaises(IntegrityError) as cm, transaction.atomic():
            emisor_prueba(**campos)
        self.assertIn(nombre, str(cm.exception))

    def test_cuit_de_10_digitos_no_entra(self):
        self.falla("emisor_cuit_formato", cuit="2000000001")

    def test_cuit_repetido_no_entra(self):
        emisor_prueba()
        self.falla("emisor_cuit_unico")

    def test_punto_de_venta_0_y_100000_no_entran(self):
        self.falla("emisor_punto_venta_rango", punto_venta=0)
        self.falla("emisor_punto_venta_rango", punto_venta=100000)

    def test_punto_de_venta_1_y_99999_entran(self):
        emisor_prueba(punto_venta=1)
        emisor_prueba(punto_venta=99999, cuit="20000000002")

    def test_valores_por_defecto(self):
        e = emisor_prueba()
        self.assertEqual(
            (e.concepto, e.descripcion, e.ambiente, e.estado),
            ("servicios", "Servicios odontológicos", "prueba", "activo"),
        )
        self.assertEqual(str(e), "Clínica Prueba S.A.S. (20000000001)")

    def test_codigos_de_arca_por_tipo(self):
        esperado = {"a": (1, 3), "b": (6, 8), "c": (11, 13)}
        for tipo, (factura, nota) in esperado.items():
            e = Emisor(tipo_comprobante=tipo)
            self.assertEqual((e.codigo_factura, e.codigo_nota_credito), (factura, nota))

    def test_alicuota_id_de_arca(self):
        self.assertEqual(Emisor.ALICUOTA_ID, {"0": 3, "10.5": 4, "21": 5, "27": 6})

    def por_vencer(self, dias, hora=10):
        vence = AHORA.replace(hour=hora) + timedelta(days=dias)
        with patch("django.utils.timezone.now", return_value=AHORA):
            return Emisor(certificado_vence=vence).por_vencer

    def test_por_vencer_a_31_dias_no(self):
        self.assertFalse(self.por_vencer(31))

    def test_por_vencer_a_30_y_29_dias_si(self):
        self.assertTrue(self.por_vencer(30))
        self.assertTrue(self.por_vencer(29))

    def test_por_vencer_hoy_y_ya_vencido_si(self):
        self.assertTrue(self.por_vencer(0))
        self.assertTrue(self.por_vencer(-1))

    def test_por_vencer_compara_fechas_argentinas_no_horas(self):
        # 31 días después, 23:59 ARG: la fecha ARG está a 31 días, no es por vencer.
        self.assertFalse(self.por_vencer(31, hora=23))
        # 30 días después, 00:01 ARG: a 30 días justos.
        self.assertTrue(self.por_vencer(30, hora=0))

    def test_sin_certificado_no_es_por_vencer(self):
        self.assertFalse(Emisor().por_vencer)

    def test_emisor_no_se_borra(self):
        e = emisor_prueba()
        with self.assertRaises(ProtectedError):
            e.delete()
        with self.assertRaises(ProtectedError):
            type(e).objects.all().delete()

    def test_emisor_con_comprobantes_no_se_borra_ni_por_la_base(self):
        e = emisor_prueba()
        factura_prueba(cobro_facturable(), emisor=e)
        with self.assertRaises(ProtectedError):
            e.delete()


class ComprobanteTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()
        self.cobro = cobro_facturable()

    def falla(self, nombre, cobro=None, **campos):
        with self.assertRaises(IntegrityError) as cm, transaction.atomic():
            factura_prueba(cobro or self.cobro, emisor=self.emisor, **campos)
        self.assertIn(nombre, str(cm.exception))

    def emitido(self, **campos):
        datos = dict(
            estado="emitido",
            cbte_tipo=6,
            punto_venta=3,
            numero=42,
            cae="70000000000001",
            cae_vence=date(2026, 10, 30),
            fecha=date(2026, 10, 20),
            neto=D("53719.01"),
            iva=D("11280.99"),
            total=D("65000.00"),
        )
        return datos | campos

    def test_emitido_completo_entra(self):
        f = factura_prueba(self.cobro, emisor=self.emisor, **self.emitido())
        self.assertEqual(f.total, D("65000.00"))

    def test_segunda_factura_vigente_del_mismo_cobro_no_entra(self):
        factura_prueba(self.cobro, emisor=self.emisor)
        self.falla("comprobante_factura_vigente_unica")

    def test_factura_nueva_despues_de_vigente_false_entra(self):
        primera = factura_prueba(self.cobro, emisor=self.emisor)
        ComprobanteFiscal.objects.filter(pk=primera.pk).update(vigente=False)
        factura_prueba(self.cobro, emisor=self.emisor)
        self.assertEqual(self.cobro.comprobantes_fiscales.count(), 2)

    def test_numero_repetido_no_entra(self):
        factura_prueba(self.cobro, emisor=self.emisor, **self.emitido())
        self.falla("comprobante_numero_unico", cobro=cobro_facturable(), **self.emitido())

    def test_mismo_numero_en_otro_tipo_de_comprobante_entra(self):
        factura_prueba(self.cobro, emisor=self.emisor, **self.emitido())
        factura_prueba(cobro_facturable(), emisor=self.emisor, **self.emitido(cbte_tipo=1))

    def test_nota_de_credito_sin_asociado_no_entra(self):
        self.falla("comprobante_asociado_segun_tipo", tipo="nota_credito")

    def test_factura_con_asociado_no_entra(self):
        otra = factura_prueba(cobro_facturable(), emisor=self.emisor)
        self.falla("comprobante_asociado_segun_tipo", asociado=otra)

    def test_segunda_nota_de_credito_de_la_factura_no_entra(self):
        factura = factura_prueba(self.cobro, emisor=self.emisor)
        factura_prueba(self.cobro, emisor=self.emisor, tipo="nota_credito", asociado=factura)
        self.falla("comprobante_nota_credito_unica", tipo="nota_credito", asociado=factura)

    def test_nota_de_credito_nueva_si_la_anterior_esta_descartada(self):
        factura = factura_prueba(self.cobro, emisor=self.emisor)
        nota = factura_prueba(self.cobro, emisor=self.emisor, tipo="nota_credito", asociado=factura)
        ComprobanteFiscal.objects.filter(pk=nota.pk).update(estado="descartado")
        factura_prueba(self.cobro, emisor=self.emisor, tipo="nota_credito", asociado=factura)

    def test_emitido_sin_cae_no_entra(self):
        self.falla("comprobante_emitido_completo", **self.emitido(cae=""))

    def test_emitido_sin_numero_fecha_total_o_vencimiento_no_entra(self):
        for campo in ("numero", "fecha", "total", "cae_vence"):
            with self.subTest(campo=campo):
                self.falla("comprobante_emitido_completo", **self.emitido(**{campo: None}))

    def test_error_sin_motivo_no_entra(self):
        self.falla("comprobante_error_con_motivo", estado="error")

    def test_error_con_motivo_entra(self):
        factura_prueba(self.cobro, emisor=self.emisor, estado="error", motivo="Rechazado")

    def test_intentos_6_entra_y_7_no(self):
        factura_prueba(self.cobro, emisor=self.emisor, intentos=6)
        self.falla("comprobante_intentos_max", cobro=cobro_facturable(), intentos=7)

    def test_intentos_cero_entra(self):
        self.assertEqual(factura_prueba(self.cobro, emisor=self.emisor).intentos, 0)

    def test_servicio_desde_posterior_a_hasta_no_entra(self):
        self.falla(
            "comprobante_servicio_orden",
            servicio_desde=date(2026, 10, 21),
            servicio_hasta=date(2026, 10, 20),
        )

    def test_servicio_desde_igual_a_hasta_entra(self):
        factura_prueba(
            self.cobro,
            emisor=self.emisor,
            servicio_desde=date(2026, 10, 20),
            servicio_hasta=date(2026, 10, 20),
        )

    def test_servicio_con_un_extremo_nulo_entra(self):
        factura_prueba(self.cobro, emisor=self.emisor, servicio_desde=date(2026, 10, 20))

    def test_neto_mas_iva_distinto_de_total_no_entra(self):
        self.falla("comprobante_emitido_suma", **self.emitido(total=D("65000.01")))
        self.falla("comprobante_emitido_suma", **self.emitido(total=D("64999.99")))

    def test_comprobante_no_se_borra(self):
        f = factura_prueba(self.cobro, emisor=self.emisor)
        with self.assertRaises(ProtectedError):
            f.delete()

    def test_en_curso_segun_en_curso_desde(self):
        f = factura_prueba(self.cobro, emisor=self.emisor)
        self.assertFalse(f.en_curso)
        f.en_curso_desde = self.cobro.fecha
        self.assertTrue(f.en_curso)
