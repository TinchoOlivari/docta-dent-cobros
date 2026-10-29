from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.auditoria.servicios import historial_de
from sistema.cobros.models import PagoComplementario
from sistema.cobros.tests.fabrica import cobro_prueba, pago_prueba, topes_desde_enero
from sistema.cobros.tests.test_comprobante import imagen
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
OCT = date(2026, 10, 1)
EN_OCT = datetime(2026, 10, 15, 12, 0, tzinfo=ARG)
CBU = "0000000000000000000001"


def en(dia, mes=11):
    return mock.patch(
        "django.utils.timezone.now", return_value=datetime(2026, mes, dia, 10, 0, tzinfo=ARG)
    )


class BasePantalla(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = en(10)
        reloj.start()
        self.addCleanup(reloj.stop)
        User = get_user_model()
        self.admin = User.objects.create_user("admin", email="a@example.com", role="administrator")
        self.jimena = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.jimena,
            alias="jimena.roble.rana",
            cbu=CBU,
            tope_mensual=D("600000.00"),
        )
        topes_desde_enero()
        self.client.force_login(self.admin)

    def cobro(self, destinatario, cuenta, monto):
        return cobro_prueba(
            destinatario=destinatario,
            cuenta=cuenta,
            monto=D(monto),
            fecha=EN_OCT,
            estado="finalizado",
        )

    def datos(self, **kw):
        d = {
            "destinatario": self.jimena.pk,
            "mes": "2026-10",
            "monto": "30000,00",
            "metodo": "transferencia",
            "cuenta": self.cuenta.pk,
        }
        d.update(kw)
        return d

    def listado(self):
        return self.client.get(reverse("cobros:complementarios"), {"mes": "2026-10"})


class ListadoTest(BasePantalla):
    def test_us1_1_muestra_esperado_recibido_falta_y_cbu(self):
        self.cobro(self.jimena, self.cuenta, "570000.00")
        r = self.listado()
        self.assertEqual(r.status_code, 200)
        for texto in ("$600.000,00", "$570.000,00", "$30.000,00", "jimena.roble.rana", CBU):
            self.assertContains(r, texto)

    def test_us1_5_con_falta_no_muestra_a_quien_no_le_falta(self):
        rodrigo = crear_destinatario(usuario=None, nombre="Rodrigo", tipo="profesional")
        c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=rodrigo,
            alias="rodrigo.alias",
            tope_mensual=D("500000.00"),
        )
        topes_desde_enero()
        self.cobro(rodrigo, c, "500000.00")
        self.cobro(self.jimena, self.cuenta, "570000.00")
        r = self.client.get(reverse("cobros:complementarios"), {"mes": "2026-10", "con_falta": 1})
        self.assertContains(r, "jimena.roble.rana")
        self.assertNotContains(r, "rodrigo.alias")

    def test_sin_mes_el_dia_10_propone_el_mes_anterior(self):
        r = self.client.get(reverse("cobros:complementarios"))
        self.assertContains(r, 'value="2026-10"')

    def test_mes_invalido_usa_el_propuesto(self):
        r = self.client.get(reverse("cobros:complementarios"), {"mes": "abc"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="2026-10"')


class AltaTest(BasePantalla):
    def nuevo(self, **kw):
        return self.client.get(reverse("cobros:complementario_nuevo"), kw)

    def test_us2_5_mes_por_defecto_dia_10_es_octubre(self):
        self.assertContains(self.nuevo(), 'value="2026-10"')

    def test_us2_5_mes_por_defecto_dia_11_es_noviembre(self):
        with en(11):
            self.assertContains(self.nuevo(), 'value="2026-11"')

    def test_post_redirige_al_detalle_y_la_falta_queda_en_cero(self):
        self.cobro(self.jimena, self.cuenta, "570000.00")
        r = self.client.post(reverse("cobros:complementario_nuevo"), self.datos())
        pago = PagoComplementario.objects.get()
        self.assertRedirects(r, reverse("cobros:complementario_detalle", args=[pago.pk]))
        self.assertContains(self.listado(), "$0,00")

    def test_mes_posterior_al_corriente_vuelve_al_form_con_el_error(self):
        with en(15):
            r = self.client.post(reverse("cobros:complementario_nuevo"), self.datos(mes="2026-12"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "El mes no puede ser posterior al mes corriente.")
        self.assertEqual(PagoComplementario.objects.count(), 0)

    def test_post_con_imagen_guarda_el_comprobante_y_se_abre(self):
        self.client.post(reverse("cobros:complementario_nuevo"), self.datos(comprobante=imagen()))
        pago = PagoComplementario.objects.get()
        self.assertTrue(pago.comprobante)
        r = self.client.get(reverse("cobros:complementario_comprobante", args=[pago.pk]))
        self.assertEqual(r.status_code, 200)

    def test_sin_comprobante_es_404(self):
        pago = pago_prueba(
            destinatario=self.jimena, cuenta=self.cuenta, monto=D("1.00"), creado_por=self.admin
        )
        r = self.client.get(reverse("cobros:complementario_comprobante", args=[pago.pk]))
        self.assertEqual(r.status_code, 404)


class CorreccionTest(BasePantalla):
    def setUp(self):
        super().setUp()
        self.pago = pago_prueba(
            destinatario=self.jimena, cuenta=self.cuenta, monto=D("30000.00"), creado_por=self.admin
        )

    def test_anular_marca_anulado_y_redirige(self):
        r = self.client.post(
            reverse("cobros:complementario_anular", args=[self.pago.pk]),
            {"motivo": "datos_erroneos"},
        )
        self.assertRedirects(r, reverse("cobros:complementario_detalle", args=[self.pago.pk]))
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.estado, "anulado")

    def test_get_de_anular_es_405(self):
        r = self.client.get(reverse("cobros:complementario_anular", args=[self.pago.pk]))
        self.assertEqual(r.status_code, 405)

    def test_editar_el_monto_deja_evento_con_el_cambio(self):
        self.client.post(
            reverse("cobros:complementario_editar", args=[self.pago.pk]),
            self.datos(monto="25000,00"),
        )
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.monto, D("25000.00"))
        self.assertTrue(historial_de(self.pago).filter(tipo="cambio").exists())

    def test_editar_un_anulado_redirige_con_el_mensaje(self):
        self.client.post(
            reverse("cobros:complementario_anular", args=[self.pago.pk]),
            {"motivo": "no_pago"},
        )
        url = reverse("cobros:complementario_editar", args=[self.pago.pk])
        r = self.client.get(url, follow=True)
        self.assertContains(r, "Un pago complementario Anulado no se edita.")
