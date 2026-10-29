from django.urls import NoReverseMatch, reverse


from ..models import Regla
from . import ReglasTestCase


class PantallaTest(ReglasTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def datos(self, **extra):
        d = {
            "nombre": "Rodrigo primero los lunes",
            "cuenta": self.rodrigo.pk,
            "estado": "activa",
            "disparador": "semana",
            "dia_semana": "1",
            "hora": "08:00",
            "accion": "primero",
        }
        d.update(extra)
        return d

    def test_nueva_crea_regla_activa(self):
        r = self.client.post(reverse("automatizacion:nueva"), self.datos())
        self.assertRedirects(r, reverse("automatizacion:lista"))
        regla = Regla.objects.get()
        self.assertEqual((regla.estado, regla.dia), ("activa", 1))

    def test_dia_del_mes_32_muestra_error(self):
        r = self.client.post(
            reverse("automatizacion:nueva"),
            self.datos(disparador="mes", dia_semana="", dia_mes="32"),
        )
        self.assertContains(r, "El día debe ir de 1 a 31.")
        self.assertEqual(Regla.objects.count(), 0)

    def test_dia_del_mes_31_y_1_valen(self):
        for dia in ("1", "31"):
            r = self.client.post(
                reverse("automatizacion:nueva"),
                self.datos(disparador="mes", dia_semana="", dia_mes=dia, nombre=f"m{dia}"),
            )
            self.assertEqual(r.status_code, 302)

    def test_dia_del_mes_0_muestra_error(self):
        r = self.client.post(
            reverse("automatizacion:nueva"),
            self.datos(disparador="mes", dia_semana="", dia_mes="0"),
        )
        self.assertContains(r, "El día debe ir de 1 a 31.")

    def test_falta_el_dia(self):
        r = self.client.post(reverse("automatizacion:nueva"), self.datos(dia_semana=""))
        self.assertContains(r, "Elegí el día.")

    def test_posicion_sin_numero(self):
        r = self.client.post(reverse("automatizacion:nueva"), self.datos(accion="posicion"))
        self.assertContains(r, "La posición específica debe ser 1 o más.")

    def test_select_de_cuenta_no_ofrece_pos(self):
        r = self.client.get(reverse("automatizacion:nueva"))
        self.assertNotContains(r, f'value="{self.pos.pk}"')
        self.assertContains(r, f'value="{self.rodrigo.pk}"')

    def test_regla_repetida_activa_da_error_en_el_form(self):
        self.client.post(reverse("automatizacion:nueva"), self.datos())
        r = self.client.post(reverse("automatizacion:nueva"), self.datos(nombre="Otra"))
        self.assertContains(r, "Ya hay una regla Activa")
        self.assertEqual(Regla.objects.count(), 1)

    def test_editar_precarga_dia_y_guarda(self):
        regla = self.regla(cuenta=self.rodrigo)
        url = reverse("automatizacion:editar", args=[regla.pk])
        r = self.client.get(url)
        self.assertNotContains(r, 'name="estado"')
        r = self.client.post(url, self.datos(dia_semana="2"))
        self.assertRedirects(r, reverse("automatizacion:lista"))
        regla.refresh_from_db()
        self.assertEqual(regla.dia, 2)

    def test_estado_desactiva_dos_reglas(self):
        a = self.regla(cuenta=self.rodrigo, nombre="a")
        b = self.regla(cuenta=self.jimena, nombre="b")
        r = self.client.post(
            reverse("automatizacion:estado"),
            {"pks": [a.pk, b.pk], "estado": "inactiva"},
            follow=True,
        )
        self.assertContains(r, "2 reglas desactivadas.")
        self.assertEqual(Regla.objects.filter(estado="inactiva").count(), 2)

    def test_estado_activa(self):
        a = self.regla(cuenta=self.rodrigo, estado="inactiva")
        r = self.client.post(
            reverse("automatizacion:estado"), {"pks": [a.pk], "estado": "activa"}, follow=True
        )
        self.assertContains(r, "1 regla activada.")

    def test_estado_invalido_da_400(self):
        r = self.client.post(reverse("automatizacion:estado"), {"pks": [], "estado": "borrar"})
        self.assertEqual(r.status_code, 400)

    def test_estado_por_get_da_405(self):
        self.assertEqual(self.client.get(reverse("automatizacion:estado")).status_code, 405)

    def test_no_hay_urls_de_borrar_ni_ejecutar(self):
        for nombre in ("automatizacion:borrar", "automatizacion:ejecutar"):
            with self.assertRaises(NoReverseMatch):
                reverse(nombre)
