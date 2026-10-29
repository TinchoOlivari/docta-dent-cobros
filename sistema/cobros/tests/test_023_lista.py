"""023 A: lista de cobros con menú "Acciones (N)", fecha en una línea y textos de filtros."""

from django.urls import reverse

from sistema.cobros.tests.test_018_lista import Base018Lista


class Lista023Test(Base018Lista):
    def test_el_operador_no_ve_el_menu(self):
        self.cobro()
        html = self.html(self.ana)
        self.assertNotIn("Descargar CSV", html)
        self.assertNotIn("Cancelar elegidos", html)

    def test_post_descargar_xlsx_por_item_sigue_dando_excel(self):
        c = self.cobro()
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("reportes:cobros_elegidos"),
            {"accion": "descargar", "formato": "xlsx", "por": "item", "pk": [c.pk]},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r["Content-Type"])
