from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG

User = get_user_model()
CUENTA = "destinatarios.cuenta"
DEST = "destinatarios.destinatario"


def arg(mes, dia, h=0, m=0, s=0, año=2026):
    return datetime(año, mes, dia, h, m, s, tzinfo=ARG)


class EventosBase(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "maria", email="m@test.com", password="x", role="administrator"
        )
        self.ana = User.objects.create_user(
            "ana", email="a@test.com", password="x", role="operator"
        )
        self.luis = User.objects.create_user(
            "luis", email="l@test.com", password="x", role="operator"
        )
        self.client.force_login(self.admin)

    def ev(self, fecha, usuario=None, entidad=CUENTA, tipo="cambio", **kw):
        return Evento.objects.create(
            tipo=tipo, fecha=fecha, usuario=usuario, entidad=entidad, entidad_id=1, **kw
        )

    def ids(self, **params):
        r = self.client.get(reverse("auditoria:eventos"), params)
        self.assertEqual(r.status_code, 200)
        return {e.pk for e in r.context["pagina"]}, r


class FiltrosTest(EventosBase):
    def test_filtro_usuario_entidad_y_octubre(self):
        ok = self.ev(arg(10, 15), self.ana)
        self.ev(arg(10, 15), self.luis)
        self.ev(arg(10, 15), self.ana, entidad=DEST)
        self.ev(arg(9, 15), self.ana)
        self.ev(arg(11, 15), self.ana)
        ids, _ = self.ids(
            usuario=self.ana.pk, entidad=CUENTA, desde="2026-10-01", hasta="2026-10-31"
        )
        self.assertEqual(ids, {ok.pk})

    def test_limites_de_dia_en_hora_argentina(self):
        entra_fin = self.ev(arg(10, 31, 23, 59, 59))
        entra_ini = self.ev(arg(10, 1, 0, 0, 0))
        self.ev(arg(11, 1, 0, 0, 0))
        self.ev(arg(9, 30, 23, 59, 59))
        self.assertEqual(entra_fin.fecha.astimezone(timezone.utc).hour, 2)  # 02:59:59 UTC del 1/11
        ids, _ = self.ids(desde="2026-10-01", hasta="2026-10-31", tipo="cambio")
        self.assertEqual(ids, {entra_fin.pk, entra_ini.pk})

    def test_usuario_sistema_excluye_login_fallido(self):
        sistema = self.ev(arg(10, 1), None, tipo="ejecucion_regla")
        self.ev(arg(10, 1), None, tipo="login_fallido", usuario_intentado="pepe", entidad="")
        self.ev(arg(10, 1), self.ana)
        ids, _ = self.ids(usuario="sistema")
        self.assertEqual(ids, {sistema.pk})

    def test_usuario_ana_incluye_sus_login_fallidos(self):
        f = self.ev(arg(10, 1), self.ana, tipo="login_fallido", usuario_intentado="ana")
        self.ev(arg(10, 1), self.luis)
        ids, _ = self.ids(usuario=self.ana.pk)
        self.assertEqual(ids, {f.pk})

    def test_filtro_tipo(self):
        f = self.ev(arg(10, 1), self.ana, tipo="login_fallido")
        self.ev(arg(10, 1), self.ana)
        ids, _ = self.ids(tipo="login_fallido")
        self.assertEqual(ids, {f.pk})

    def test_desde_posterior_a_hasta_da_error_y_cero_eventos(self):
        a = self.ev(arg(10, 15), self.ana)
        b = self.ev(arg(10, 16), self.ana)
        ids, r = self.ids(desde="2026-10-31", hasta="2026-10-01")
        self.assertEqual(ids, set())  # filtro inválido: 0 eventos (020/FR-024)

    def test_misma_fecha_desde_y_hasta_es_valida(self):
        ok = self.ev(arg(10, 15, 12))
        ids, _ = self.ids(desde="2026-10-15", hasta="2026-10-15")
        self.assertEqual(ids, {ok.pk})

    def test_paginado_50(self):
        for i in range(51):
            self.ev(arg(10, 15, 0, 0, i), self.ana)
        url = reverse("auditoria:eventos")
        r = self.client.get(url, {"usuario": self.ana.pk})
        self.assertEqual(len(r.context["pagina"]), 50)
        r = self.client.get(url, {"usuario": self.ana.pk, "page": 2})
        self.assertEqual(len(r.context["pagina"]), 1)


class PermisosTest(EventosBase):
    def test_acceso_por_rol(self):
        url = reverse("auditoria:eventos")
        pro = User.objects.create_user("pro", email="p@test.com", password="x", role="professional")
        for usuario, codigo in ((self.admin, 200), (self.ana, 403), (pro, 403)):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(url).status_code, codigo)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
