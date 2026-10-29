"""018 FR-030 a FR-033: arreglos de la revisión de 017."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from sistema.auditoria.servicios import historial_de
from sistema.cobros.models import Cobro
from sistema.cobros.tests.test_017_operador import Base017

HTMX = {"HTTP_HX_REQUEST": "true"}
LISTA = reverse("cobros:lista")


class RevisadoHtmxTest(Base017):
    def post(self, cobro, htmx=True, **datos):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("cobros:revisado", args=[cobro.pk]), datos, **(HTMX if htmx else {})
        )

    def test_excedente_sin_comentario_devuelve_el_error_en_la_misma_respuesta(self):
        c = self.cobro(excedente=True)
        for comentario in ("", "   "):
            html = self.post(c, comentario=comentario).content.decode()
            self.assertIn("Escribí un comentario.", html)
            self.assertIn('role="alert"', html)
        c.refresh_from_db()
        self.assertFalse(c.revisado)
        self.assertNotContains(self.client.get(LISTA), "Escribí un comentario.")

    def test_el_operador_no_puede_marcar_revisado(self):
        c = self.cobro()
        self.client.force_login(self.ana)
        r = self.client.post(reverse("cobros:revisado", args=[c.pk]), {}, **HTMX)
        self.assertEqual(r.status_code, 403)


class CancelarLoteDobleEnvioTest(Base017):
    def test_dos_post_seguidos_el_segundo_omite_ambos_y_hay_un_evento_por_cobro(self):
        a, b = self.cobro(), self.cobro()
        self.client.force_login(self.admin)
        datos = {"pk": [a.pk, b.pk], "motivo": "datos_erroneos", "next": LISTA}
        url = reverse("cobros:cancelar_lote")
        r1 = self.client.post(url, datos, follow=True)
        self.assertContains(r1, "Cancelados: 2. Omitidos: 0.")
        r2 = self.client.post(url, datos, follow=True)
        self.assertContains(r2, "Cancelados: 0. Omitidos: 2.")
        for c in (a, b):
            c.refresh_from_db()
            self.assertEqual(c.estado, "cancelado")
            self.assertEqual(historial_de(c).filter(tipo="cambio").count(), 1)


class AntiguedadOperadorTest(Base017):
    def lista(self, **params):
        self.client.force_login(self.ana)
        return self.client.get(LISTA, params)

    def test_ventana_de_15_dias_14_dias_23_horas_visible_y_15_dias_1_hora_no(self):
        c = self.cobro()
        ahora = timezone.now()
        Cobro.objects.filter(pk=c.pk).update(fecha_alta=ahora - timedelta(days=14, hours=23))
        self.assertEqual(len(self.lista(estado="pendiente").context["pagina"]), 1)
        Cobro.objects.filter(pk=c.pk).update(fecha_alta=ahora - timedelta(days=15, hours=1))
        self.assertEqual(len(self.lista(estado="pendiente").context["pagina"]), 0)

    def test_pendientes_sin_orden_van_del_mas_viejo_al_mas_nuevo(self):
        ahora = timezone.now()
        pks = []
        for horas in (1, 30, 10):
            c = self.cobro()
            Cobro.objects.filter(pk=c.pk).update(fecha_alta=ahora - timedelta(hours=horas))
            pks.append(c.pk)
        r = self.lista(estado="pendiente")
        self.assertEqual([c.pk for c in r.context["pagina"]], [pks[1], pks[2], pks[0]])
