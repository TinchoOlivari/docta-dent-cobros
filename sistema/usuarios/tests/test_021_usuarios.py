"""021 FR-046, FR-050, FR-051, FR-052 y FR-062: Usuarios, Panel y profesional."""

from django.urls import reverse

from payment_instructions.models import User
from sistema.auditoria.forms import FiltroEventosForm
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import registrar
from sistema.cobros.models import ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario
from sistema.dentalink.models import Profesional
from sistema.usuarios.models import Perfil


def _perfil(usuario, **campos):
    return Perfil.objects.create(usuario=usuario, **campos)


class Base(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.adm = User.objects.create_user(
            "adm", email="a@example.com", password="x", role="administrator"
        )
        _perfil(self.adm)
        self.client.force_login(self.adm)


class ListaTest(Base):
    def test_usuario_completar_no_aparece_en_la_lista_ni_en_el_filtro_de_auditoria(self):
        comp = User.objects.create_user(
            "completar", email="c@example.com", password=None, role="operator", is_active=False
        )
        registrar(Evento.Tipo.ALTA, usuario=comp, entidad=self.adm)
        r = self.client.get(reverse("usuarios:lista"))
        self.assertNotContains(r, "completar")
        valores = [v for v, _ in FiltroEventosForm().fields["usuario"].choices]
        self.assertNotIn(str(comp.pk), valores)
        self.assertIn(str(self.adm.pk), valores)
        # sus eventos siguen
        self.assertTrue(Evento.objects.filter(usuario=comp).exists())
        self.assertContains(self.client.get(reverse("auditoria:eventos")), "completar")

    def test_inactivo_con_perfil_si_aparece(self):
        baja = User.objects.create_user(
            "baja", email="b@example.com", password="x", role="operator", is_active=False
        )
        _perfil(baja)
        self.assertContains(self.client.get(reverse("usuarios:lista")), "baja")

    def test_busqueda_sin_acentos_ni_mayusculas(self):
        n = User.objects.create_user(
            "nuniez", email="n@example.com", password="x", role="operator", first_name="Núñez"
        )
        _perfil(n)
        for q in ("nunez", "NÚÑEZ", "núñez"):
            with self.subTest(q=q):
                self.assertContains(self.client.get(reverse("usuarios:lista"), {"q": q}), "nuniez")

    def _consultas(self, desde, hasta):
        for i in range(desde, hasta):
            u = User.objects.create_user(
                f"u{i}", email=f"u{i}@example.com", password="x", role="operator"
            )
            _perfil(u)
        self.client.get(reverse("usuarios:lista"))  # calienta cachés (sesión, permisos)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as c:
            self.client.get(reverse("usuarios:lista"))
        return len(c)

    def test_consultas_constantes_con_13_y_30_usuarios(self):
        con_13 = self._consultas(0, 12)  # + adm
        con_30 = self._consultas(12, 29)
        self.assertEqual(con_13, con_30)


class EdicionTest(Base):
    def test_editar_profesional_vincula(self):
        prof = User.objects.create_user(
            "dra", email="d@example.com", password="x", role="professional"
        )
        p = Profesional.objects.create(id_dentalink=77, nombre="Dra. Inventada", vigente=True)
        url = reverse("usuarios:editar", args=[prof.pk])
        r = self.client.post(url, {"role": "professional", "profesional_dentalink": p.pk})
        self.assertRedirects(r, reverse("usuarios:lista"))
        perfil = Perfil.objects.get(usuario=prof)
        self.assertEqual(
            (perfil.id_profesional_dentalink, perfil.nombre_profesional_dentalink),
            (77, "Dra. Inventada"),
        )

    def test_editar_profesional_con_vinculo_fuera_del_catalogo_no_lo_borra(self):
        prof = User.objects.create_user(
            "dra", email="d@example.com", password="x", role="professional"
        )
        _perfil(prof, id_profesional_dentalink=88, nombre_profesional_dentalink="Dra. Migrada")
        url = reverse("usuarios:editar", args=[prof.pk])
        r = self.client.post(url, {"role": "professional", "first_name": "Nuevo"})
        self.assertRedirects(r, reverse("usuarios:lista"))
        perfil = Perfil.objects.get(usuario=prof)
        self.assertEqual(
            (perfil.id_profesional_dentalink, perfil.nombre_profesional_dentalink),
            (88, "Dra. Migrada"),
        )


class PanelTest(Base):
    def _item(self, estado):
        dest = crear_destinatario(usuario=None, nombre="Destino", tipo="clinica")
        cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="alias.prueba",
            cbu="0000000000000000000009",
            tope_mensual="600000.00",
        )
        c = cobro_prueba(cuenta=cuenta, monto="1000.00", estado="finalizado")
        return ItemCobro.objects.create(cobro=c, numero=1, monto="1000.00", estado_envio=estado)

    def test_un_item_para_revisar_cuenta_1_con_enlace_a_envios(self):
        self._item("revisar")
        r = self.client.get(reverse("usuarios:panel"))
        fila = [p for p in r.context["pendientes"] if p[0] == "Ítems para revisar en Dentalink"][0]
        self.assertEqual(fila[1:], (1, reverse("dentalink:envios") + "?estado=revisar"))

    def test_sin_items_para_revisar_cuenta_0(self):
        r = self.client.get(reverse("usuarios:panel"))
        fila = [p for p in r.context["pendientes"] if p[0] == "Ítems para revisar en Dentalink"][0]
        self.assertEqual(fila[1], 0)
