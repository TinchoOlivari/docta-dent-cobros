from datetime import datetime
from unittest import mock

from django.urls import reverse

from payment_instructions.models import User
from sistema.cobros.models import ItemCobro
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_destinatario, editar_destinatario
from sistema.usuarios.models import Perfil

from . import EN_OCT, D, DatosMartinO

NOV_5 = datetime(2026, 11, 5, 10, 0, tzinfo=ARG)


class ProfesionalBase(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.drax = User.objects.create_user("drax", email="drax@example.com", role="professional")
        Perfil.objects.create(
            usuario=self.drax, id_profesional_dentalink=501, nombre_profesional_dentalink="Dra. X"
        )
        self.url = reverse("usuarios:profesional")
        self.client.force_login(self.drax)

    def item(self, cuenta, monto, prof=501, fecha=EN_OCT, **kw):
        c = self.cobro(cuenta, monto, fecha=fecha, **kw)
        ItemCobro.objects.create(
            cobro=c, numero=1, id_prestacion=c.pk, id_profesional=prof, monto=D(monto)
        )
        return c


class ItemsDelMesTest(ProfesionalBase):
    def test_12_items_total_y_cancelado_no_suma(self):
        for _ in range(12):
            self.item(self.tincho, "40000.00")
        r = self.client.get(self.url, {"mes": "2026-10"})
        self.assertEqual(len(r.context["pagina"]), 12)
        self.assertContains(r, "Total del mes: $480.000,00")
        self.item(self.tincho, "10000.00", estado="cancelado")
        r = self.client.get(self.url, {"mes": "2026-10"})
        self.assertEqual(len(r.context["pagina"]), 13)
        self.assertContains(r, "Total del mes: $480.000,00")

    def test_limite_del_mes_y_otro_profesional(self):
        self.item(self.tincho, "100.00", fecha=datetime(2026, 11, 1, 0, 0, tzinfo=ARG))
        self.item(self.tincho, "200.00", fecha=datetime(2026, 10, 31, 23, 59, 59, tzinfo=ARG))
        self.item(self.tincho, "300.00", prof=999)
        r = self.client.get(self.url, {"mes": "2026-10"})
        self.assertEqual([i.monto for i in r.context["pagina"]], [D("200.00")])
        self.assertContains(r, "Total del mes: $200,00")

    def test_mes_sin_items_total_cero(self):
        r = self.client.get(self.url, {"mes": "2026-09"})
        self.assertContains(r, "Total del mes: $0,00")

    def test_sin_mes_usa_el_corriente(self):
        self.item(self.tincho, "100.00", fecha=datetime(2026, 11, 2, 9, 0, tzinfo=ARG))
        with mock.patch("django.utils.timezone.now", return_value=NOV_5):
            self.client.force_login(self.drax)
            r = self.client.get(self.url)
        self.assertEqual(len(r.context["pagina"]), 1)

    def test_sin_id_profesional_no_muestra_total(self):
        Perfil.objects.filter(usuario=self.drax).update(
            id_profesional_dentalink=None, nombre_profesional_dentalink=""
        )
        r = self.client.get(self.url, {"mes": "2026-10"})
        self.assertNotContains(r, "Total del mes")  # 020/FR-029: sin vínculo no hay sección


class ReporteVinculadoTest(ProfesionalBase):
    def test_con_vinculo_muestra_reporte(self):
        editar_destinatario(self.martin, usuario=self.admin, usuario_profesional=self.drax)
        self.cobro(self.tincho, "100000.00")
        r = self.client.get(self.url, {"mes": "2026-10"})
        for t in ("Recibido vs. esperado", "— alias.uno", "$100.000,00"):
            self.assertContains(r, t)

    def test_sin_vinculo_no_hay_reporte_y_si_items(self):
        self.item(self.tincho, "100.00")
        r = self.client.get(self.url, {"mes": "2026-10"})
        self.assertNotContains(r, "Recibido vs. esperado")
        self.assertEqual(len(r.context["pagina"]), 1)


class AislamientoTest(ProfesionalBase):
    def test_no_ve_cuentas_ni_destinatarios_de_otros_ni_descargas(self):
        jimena = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        cuenta = self.transf(jimena, "jimena.roble.rana", "1000.00")
        cuenta.cbu = "0000000000000000000001"
        cuenta.save()
        self.item(cuenta, "100.00", destinatario=jimena)
        r = self.client.get(self.url, {"mes": "2026-10"})
        self.assertEqual(len(r.context["pagina"]), 1)
        for t in ("jimena.roble.rana", "0000000000000000000001", "Jimena"):
            self.assertNotContains(r, t)
        for n in ("cobros_descargar", "destinatarios_descargar"):
            self.assertNotContains(r, reverse(f"reportes:{n}"))
            self.assertEqual(self.client.get(reverse(f"reportes:{n}")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("reportes:destinatario", args=[jimena.pk])).status_code, 404
        )
