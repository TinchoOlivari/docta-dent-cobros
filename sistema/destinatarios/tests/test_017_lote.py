from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import (
    cambiar_estado_destinatario,
    cambiar_estado_destinatarios,
    crear_cuenta,
    crear_destinatario,
)
from sistema.terminales.models import Sesion, Terminal
from sistema.destinatarios.models import Proveedor


class LoteHelpers:
    """setUp y ayudantes de lote; sin tests, para reusar sin repetirlos."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            "adm017l", email="adm017l@example.com", role="administrator"
        )
        self.client.force_login(self.admin)
        self.url = reverse("destinatarios:estado_lote")

    def dest(self, nombre, estado="activo"):
        d = crear_destinatario(usuario=None, nombre=nombre, tipo="profesional")
        crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=d, alias=f"{nombre}.uno".lower(),
            tope_mensual=1000,
        )  # fmt: skip
        if estado != "activo":
            d = cambiar_estado_destinatario(d, estado, usuario=None)
        return d

    def con_sesion_abierta(self, d):
        proveedor = Proveedor.objects.get_or_create(nombre="Mercadopago")[0]
        pos = crear_cuenta(
            usuario=None, tipo="terminal_pos", destinatario=d, titular="Titular POS",
            proveedor=proveedor, objetivo=1000,
        )  # fmt: skip
        t = Terminal.objects.create(nombre="Terminal 017", proveedor=proveedor, acepta_debito=True)
        Sesion.objects.create(terminal=t, cuenta=pos, inicio=timezone.now())


class LoteTest(LoteHelpers, SistemaTestCase):
    def test_ejemplo_fr026_cambiados_2_omitidos_2(self):
        a, b = self.dest("Aaa"), self.dest("Bbb")
        c = self.dest("Ccc", "inactivo")
        d = self.dest("Ddd")
        self.con_sesion_abierta(d)
        r = self.client.post(
            self.url, {"pk": [a.pk, b.pk, c.pk, d.pk], "estado": "inactivo"}, follow=True
        )
        self.assertIn("Cambiados: 2. Omitidos: 2.", [str(m) for m in r.context["messages"]])
        self.assertEqual(Destinatario.objects.get(pk=d.pk).estado, "activo")

    def test_archivado_se_omite_y_no_deja_evento(self):
        a = self.dest("Aaa")
        Destinatario.objects.filter(pk=a.pk).update(estado="archivado")
        antes = Evento.objects.count()
        self.assertEqual(
            cambiar_estado_destinatarios([a.pk], "activo", usuario=self.admin), ([], [a.pk])
        )
        self.assertEqual(Evento.objects.count(), antes)

    def test_reactivar_vuelve_a_la_lista_en_orden_de_alta_sin_huecos(self):
        a, b = self.dest("Aaa"), self.dest("Bbb")
        cambiar_estado_destinatarios([b.pk, a.pk], "inactivo", usuario=self.admin)
        self.assertEqual(Cuenta.objects.filter(posicion__isnull=False).count(), 0)
        cambiar_estado_destinatarios([b.pk, a.pk], "activo", usuario=self.admin)
        posiciones = {c.destinatario_id: c.posicion for c in Cuenta.objects.all()}
        self.assertEqual(posiciones, {a.pk: 1, b.pk: 2})

    def test_un_evento_por_destinatario_cambiado(self):
        a, b = self.dest("Aaa"), self.dest("Bbb")
        antes = Evento.objects.filter(entidad="destinatarios.destinatario").count()
        cambiar_estado_destinatarios([a.pk, b.pk], "inactivo", usuario=self.admin)
        despues = Evento.objects.filter(entidad="destinatarios.destinatario").count()
        self.assertEqual(despues - antes, 2)

    def test_estado_vacio_o_desconocido_es_400(self):
        a = self.dest("Aaa")
        for estado in ("", "xx", "borrado"):
            r = self.client.post(self.url, {"pk": [a.pk], "estado": estado})
            self.assertEqual(r.status_code, 400, estado)
        self.assertEqual(Destinatario.objects.get(pk=a.pk).estado, "activo")

    def test_next_externo_cae_en_la_lista(self):
        a = self.dest("Aaa")
        r = self.client.post(
            self.url, {"pk": [a.pk], "estado": "inactivo", "next": "https://malo.example/"}
        )
        self.assertRedirects(r, reverse("destinatarios:lista"))

    def test_servicio_exige_administrador(self):
        op = get_user_model().objects.create_user("o17", email="o17@example.com", role="operator")
        with self.assertRaises(PermissionDenied):
            cambiar_estado_destinatarios([], "activo", usuario=op)

    def test_operador_profesional_y_sin_sesion(self):
        a = self.dest("Aaa")
        for rol in ("operator", "professional"):
            u = get_user_model().objects.create_user(rol, email=f"{rol}@example.com", role=rol)
            self.client.force_login(u)
            r = self.client.post(self.url, {"pk": [a.pk], "estado": "inactivo"})
            self.assertEqual(r.status_code, 403, rol)
        self.client.logout()
        r = self.client.post(self.url, {"pk": [a.pk], "estado": "inactivo"})
        self.assertIn("/login/", r["Location"])
        self.assertEqual(Destinatario.objects.get(pk=a.pk).estado, "activo")
