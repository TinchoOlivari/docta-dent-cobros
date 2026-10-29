from datetime import datetime

from django.urls import reverse

from payment_instructions.models import User
from sistema.cobros.models import Cobro, ItemCobro
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.dentalink.models import Profesional
from sistema.reportes.filtros import FiltroCobros

from . import D, EN_OCT, DatosMartinO


class FiltrosTest(DatosMartinO, SistemaTestCase):
    def filtrar(self, **datos):
        return set(
            FiltroCobros(datos, queryset=Cobro.objects.all()).qs.values_list("pk", flat=True)
        )

    def setUp(self):
        super().setUp()
        self.t1 = [self.cobro(self.tincho, "1000.00") for _ in range(3)]
        self.efe = [self.cobro(self.tincho, "1000.00", metodo="efectivo") for _ in range(2)]
        self.nov = self.cobro(self.tincho, "1000.00", fecha=datetime(2026, 11, 1, tzinfo=ARG))

    def pks(self, *cobros):
        return {c.pk for c in cobros}

    def test_rango_de_octubre_con_metodo_transferencia(self):
        r = self.filtrar(desde="2026-10-01", hasta="2026-10-31", metodo="transferencia")
        self.assertEqual(r, self.pks(*self.t1))

    def test_hasta_incluye_el_ultimo_segundo_del_dia(self):
        c = self.cobro(self.tincho, "5.00", fecha=datetime(2026, 10, 31, 23, 59, 59, tzinfo=ARG))
        self.assertIn(c.pk, self.filtrar(hasta="2026-10-31"))
        self.assertNotIn(self.nov.pk, self.filtrar(hasta="2026-10-31"))

    def test_desde_excluye_el_ultimo_segundo_del_dia_anterior(self):
        c = self.cobro(self.tincho, "5.00", fecha=datetime(2026, 9, 30, 23, 59, 59, tzinfo=ARG))
        self.assertNotIn(c.pk, self.filtrar(desde="2026-10-01"))
        self.assertIn(self.t1[0].pk, self.filtrar(desde="2026-10-01"))

    def test_estado_destinatario_cuenta_creador_terminal(self):
        canc = self.cobro(self.tincho, "9.00", estado="cancelado")
        self.assertEqual(self.filtrar(estado="cancelado"), self.pks(canc))
        otro = self.cobro(self.galicia, "9.00", creado_por=self.admin)
        self.assertEqual(self.filtrar(cuenta=self.galicia.pk), self.pks(otro))
        self.assertEqual(self.filtrar(creador=self.admin.pk), self.pks(otro))
        self.assertEqual(self.filtrar(destinatario=self.martin.pk), self.filtrar())
        pos = self.cobro(self.mp, "9.00")
        self.assertEqual(self.filtrar(terminal=pos.terminal_id), self.pks(pos))

    def test_profesional_y_estado_envio_no_duplican_el_cobro(self):
        pro = Profesional.objects.create(id_dentalink=501, nombre="Dra. X")
        c = self.t1[0]
        for n in (1, 2):
            ItemCobro.objects.create(
                cobro=c, numero=n, id_profesional=501, nombre_profesional="Dra. X", monto=D("1.00")
            )
        qs = FiltroCobros(
            {"profesional": pro.pk, "estado_envio": "pendiente"}, queryset=Cobro.objects.all()
        ).qs
        self.assertEqual(list(qs), [c])
        self.assertEqual(self.filtrar(estado_envio="enviado"), set())

    def test_marcas(self):
        exc = self.cobro(self.tincho, "9.00", excedente=True)
        rev = self.cobro(self.tincho, "9.00", excedente=True, revisado=True)
        fal = self.cobro(self.tincho, "9.00", faltan_items=True)
        self.assertEqual(self.filtrar(excedente_sin_revisar="1"), self.pks(exc))
        self.assertEqual(self.filtrar(faltan_items="1"), self.pks(fal))
        self.assertEqual(self.filtrar(revisado="1"), self.pks(rev))

    def test_busqueda_por_numero_paciente_alias_y_monto(self):
        a = self.cobro(self.galicia, "70000.00", nombre_paciente="Juan Núñez")
        b = self.cobro(self.galicia, "70000.01")
        self.assertEqual(self.filtrar(q=str(a.pk)), self.pks(a))
        self.assertEqual(self.filtrar(q="núñez"), self.pks(a))
        self.assertEqual(self.filtrar(q="alias.dos"), self.pks(a, b))
        self.assertEqual(self.filtrar(q="70.000,00"), self.pks(a))
        self.assertEqual(self.filtrar(q="70000,01"), self.pks(b))
        self.assertEqual(self.filtrar(q="70000"), self.pks(a))

    def test_busqueda_con_texto_no_numerico_ni_monto_no_rompe(self):
        self.assertEqual(self.filtrar(q="NaN"), set())
        self.assertEqual(self.filtrar(q="9" * 40), set())

    def test_fecha_basura_no_rompe_y_no_filtra_por_fecha(self):
        self.assertEqual(self.filtrar(desde="basura"), self.filtrar())
        self.assertEqual(len(self.filtrar(desde="basura")), 6)


class ListaFiltradaTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.ana = User.objects.create_user("ana", email="a@example.com", role="operator")
        self.efe = self.cobro(
            self.tincho, "1.00", metodo="efectivo", creado_por=self.ana, fecha=EN_OCT
        )
        self.tr = self.cobro(self.tincho, "2.00", creado_por=self.ana, fecha=EN_OCT)
        self.url = reverse("cobros:lista")

    def pks(self, r):
        return {c.pk for c in r.context["pagina"]}

    def test_admin_filtra_por_metodo(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.pks(self.client.get(self.url, {"metodo": "efectivo"})), {self.efe.pk})

    def test_siguiente_conserva_el_filtro(self):
        for _ in range(50):
            self.cobro(self.tincho, "1.00", metodo="efectivo")
        self.client.force_login(self.admin)
        r = self.client.get(self.url, {"metodo": "efectivo"})
        self.assertRegex(r.content.decode(), r'href="\?metodo=efectivo&amp;page=2"')

    def test_operador_ve_sus_cobros_sin_filtrar(self):
        self.client.force_login(self.ana)
        r = self.client.get(self.url, {"metodo": "efectivo"})
        self.assertEqual(self.pks(r), {self.efe.pk, self.tr.pk})
