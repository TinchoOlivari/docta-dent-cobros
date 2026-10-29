from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.arca import servicios as arca
from sistema.arca.models import ComprobanteFiscal, Emisor
from sistema.arca.tests.fabricas import AHORA, cobro_facturable, emisor_prueba, factura_prueba
from sistema.auditoria.models import Evento
from sistema.cobros.models import Cobro
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.reportes.filtros import FiltroCobros


def emitida(cobro, emisor, **campos):
    datos = dict(
        estado="emitido",
        numero=1,
        punto_venta=3,
        cae="75123456789012",
        cae_vence=AHORA.date(),
        fecha=AHORA.date(),
        total="7100.00",
    )
    return factura_prueba(cobro, emisor=emisor, **(datos | campos))


class Base(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        self.admin = get_user_model().objects.create_user(
            "adm", email="adm@example.com", role="administrator"
        )
        self.emisor = emisor_prueba()
        self.destinatario = Destinatario.objects.create(
            nombre="Clínica Prueba 2", tipo="clinica", facturacion_activa=True, emisor=self.emisor
        )
        self.cuenta = Cuenta.objects.create(destinatario=self.destinatario, tipo="efectivo")
        self.cobro = cobro_facturable(cuenta=self.cuenta, destinatario=self.destinatario)


class ReintentarEmisorTest(Base):
    def setUp(self):
        super().setUp()
        self.factura = factura_prueba(
            self.cobro, emisor=self.emisor, estado="error", motivo="10016: dato inválido"
        )

    def reintentar(self):
        return arca.reintentar(self.factura, usuario=self.admin)

    def sin_cambios(self):
        antes = Evento.objects.count()
        with self.assertRaises(ValidationError) as e:
            self.reintentar()
        self.assertEqual(Evento.objects.count(), antes)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.estado, "error")
        return e.exception.messages[0]

    def test_emisor_inactivo_no_cambia_nada(self):
        Emisor.objects.filter(pk=self.emisor.pk).update(estado="inactivo")
        self.assertEqual(
            self.sin_cambios(),
            "No se puede reintentar: el emisor «Clínica Prueba S.A.S.» está Inactivo.",
        )

    def test_certificado_vencido_el_30_09_2025_no_cambia_nada(self):
        vence = AHORA.replace(year=2025, month=9, day=30)
        Emisor.objects.filter(pk=self.emisor.pk).update(certificado_vence=vence)
        self.assertEqual(
            self.sin_cambios(),
            "No se puede reintentar: el certificado del emisor venció el 30/09/2025.",
        )

    def test_certificado_vencido_justo_ahora_es_vencido(self):
        Emisor.objects.filter(pk=self.emisor.pk).update(certificado_vence=AHORA)
        self.assertIn("venció el 20/10/2026", self.sin_cambios())

    def test_certificado_que_vence_hoy_mas_tarde_es_valido(self):
        Emisor.objects.filter(pk=self.emisor.pk).update(
            certificado_vence=AHORA + timedelta(seconds=1)
        )
        self.assertEqual(self.reintentar().estado, "pendiente")

    def test_valido_toma_el_emisor_actual_del_destinatario(self):
        nuevo = emisor_prueba(cuit="20000000002", razon_social="Otra S.A.")
        Destinatario.objects.filter(pk=self.destinatario.pk).update(emisor=nuevo)
        self.cobro.refresh_from_db()
        c = self.reintentar()
        self.assertEqual((c.estado, c.emisor_id), ("pendiente", nuevo.pk))
        evento = Evento.objects.filter(entidad_id=c.pk, tipo="cambio").latest("pk")
        self.assertIn("emisor", str(evento.__dict__) + str(getattr(evento, "cambios", "")))

    def otro_emisor_en_destinatario(self):
        nuevo = emisor_prueba(cuit="20000000002", razon_social="Otra S.A.")
        Destinatario.objects.filter(pk=self.destinatario.pk).update(emisor=nuevo)
        self.cobro.refresh_from_db()

    def test_numero_enviado_sin_respuesta_conserva_el_emisor_que_lo_envio(self):
        self.factura.numero_intentado = 42
        self.factura.save()
        self.otro_emisor_en_destinatario()
        c = self.reintentar()
        self.assertEqual(
            (c.estado, c.emisor_id, c.numero_intentado), ("pendiente", self.emisor.pk, 42)
        )

    def test_nota_de_credito_conserva_el_emisor_de_su_factura_aunque_este_inactivo(self):
        ComprobanteFiscal.objects.filter(pk=self.factura.pk).update(vigente=False)
        original = emitida(self.cobro, self.emisor, numero=2)
        self.factura = factura_prueba(
            self.cobro,
            emisor=self.emisor,
            tipo="nota_credito",
            asociado=original,
            estado="error",
            motivo="10016: dato inválido",
        )
        Emisor.objects.filter(pk=self.emisor.pk).update(estado="inactivo")
        self.otro_emisor_en_destinatario()
        c = self.reintentar()
        self.assertEqual((c.estado, c.emisor_id), ("pendiente", self.emisor.pk))

    def test_la_vista_muestra_el_error_y_no_dice_reintento_pedido(self):
        Emisor.objects.filter(pk=self.emisor.pk).update(estado="inactivo")
        self.client.force_login(self.admin)
        r = self.client.post(reverse("arca:reintentar", args=[self.factura.pk]), follow=True)
        self.assertContains(r, "está Inactivo.")
        self.assertNotContains(r, "Reintento pedido.")


class PreguntasTest(Base):
    def test_con_facturacion_activa_dice_factura_nueva(self):
        f = emitida(self.cobro, self.emisor)
        self.assertEqual(
            arca._pregunta(f, self.cobro, False),
            "¿Emitir nota de crédito por $7.100,00 y una factura nueva?",
        )
        self.assertTrue(arca.pregunta_datos(f).endswith("$7.100,00 y una factura nueva?"))

    def test_sin_facturacion_activa_dice_que_no_se_emite(self):
        Destinatario.objects.filter(pk=self.destinatario.pk).update(facturacion_activa=False)
        self.cobro.refresh_from_db()
        f = emitida(self.cobro, self.emisor)
        self.assertEqual(
            arca._pregunta(f, self.cobro, False),
            "¿Emitir nota de crédito por $7.100,00? No se emite factura nueva: "
            "el destinatario no tiene la facturación activa.",
        )


class SinFacturaVigenteFiltroTest(Base):
    def filtrar(self):
        f = FiltroCobros({"factura_con_error": "on"}, queryset=Cobro.objects.all())
        return set(f.qs.values_list("pk", flat=True))

    def test_finalizado_facturable_sin_factura_vigente_entra(self):
        emitida(self.cobro, self.emisor, vigente=False)
        self.assertIn(self.cobro.pk, self.filtrar())

    def test_no_facturable_no_entra(self):
        otro = cobro_facturable()  # destinatario sin facturación
        self.assertNotIn(otro.pk, self.filtrar())

    def test_con_factura_vigente_sin_error_no_entra(self):
        factura_prueba(self.cobro, emisor=self.emisor, estado="pendiente")
        self.assertNotIn(self.cobro.pk, self.filtrar())

    def test_la_consulta_coincide_con_facturable_y_sin_factura_vigente(self):
        casos = [
            self.cobro,
            cobro_facturable(),
            cobro_facturable(
                cuenta=self.cuenta,
                destinatario=self.destinatario,
                estado="cancelado",
                motivo_cancelacion="no_pago",
            ),
            cobro_facturable(cuenta=self.cuenta, destinatario=self.destinatario, faltan_items=True),
        ]
        for cobro in casos:
            esperado = arca.sin_factura_vigente(cobro)
            self.assertEqual(
                Cobro.objects.filter(arca.FACTURABLE_Q, pk=cobro.pk).exists(),
                arca.facturable(cobro),
            )
            self.assertEqual(cobro.pk in self.filtrar(), esperado)
