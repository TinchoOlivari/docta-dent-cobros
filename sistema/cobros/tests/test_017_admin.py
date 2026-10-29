import re
from datetime import date
from decimal import Decimal

from django.urls import reverse

from sistema.arca.tests.fabricas import factura_prueba
from sistema.auditoria.servicios import historial_de
from sistema.cobros import servicios
from sistema.cobros.models import ItemCobro
from sistema.cobros.tests.test_017_operador import Base017
from sistema.dentalink.models import Profesional

HTMX = {"HTTP_HX_REQUEST": "true"}
LISTA = "cobros:lista"


def D(valor):
    return Decimal(valor)


def eventos(cobro):
    return historial_de(cobro).filter(tipo="cambio").count()


class RevisadoTest(Base017):
    def post(self, cobro, usuario=None, **datos):
        self.client.force_login(usuario or self.admin)
        extra = datos.pop("extra", {})
        return self.client.post(reverse("cobros:revisado", args=[cobro.pk]), datos, **extra)

    def test_marcar_sin_excedente_no_pide_comentario_y_deja_un_evento(self):
        cobro = self.cobro()
        self.post(cobro)
        cobro.refresh_from_db()
        self.assertTrue(cobro.revisado)
        self.assertEqual(eventos(cobro), 1)

    def test_desmarcar_limpia_el_comentario_y_deja_un_evento(self):
        cobro = self.cobro(revisado=True, comentario_revision="ok", excedente=True)
        self.post(cobro, revisado="0")
        cobro.refresh_from_db()
        self.assertFalse(cobro.revisado)
        self.assertEqual(cobro.comentario_revision, "")
        self.assertEqual(eventos(cobro), 1)

    def test_marcar_dos_veces_no_agrega_evento(self):
        cobro = self.cobro()
        self.post(cobro)
        self.post(cobro)
        self.assertEqual(eventos(cobro), 1)

    def test_con_excedente_comentario_vacio_o_en_blanco_se_rechaza_y_con_texto_marca(self):
        cobro = self.cobro(excedente=True)
        for comentario in ("", "   "):
            self.post(cobro, comentario=comentario)
            cobro.refresh_from_db()
            self.assertFalse(cobro.revisado)
        self.assertEqual(eventos(cobro), 0)
        self.post(cobro, comentario="ok")
        cobro.refresh_from_db()
        self.assertTrue(cobro.revisado)
        self.assertEqual(cobro.comentario_revision, "ok")

    def test_sin_htmx_redirige_a_next(self):
        cobro = self.cobro()
        r = self.post(cobro, next="/sistema/cobros/?estado=pendiente")
        self.assertRedirects(r, "/sistema/cobros/?estado=pendiente")

    def test_next_de_otro_dominio_va_al_detalle(self):
        cobro = self.cobro()
        r = self.post(cobro, next="https://otro.dominio/")
        self.assertRedirects(r, reverse("cobros:detalle", args=[cobro.pk]))

    def test_operador_profesional_y_anonimo(self):
        cobro = self.cobro()
        self.assertEqual(self.post(cobro, self.ana).status_code, 403)
        self.assertEqual(self.post(cobro, self.prof).status_code, 403)
        self.client.logout()
        r = self.client.post(reverse("cobros:revisado", args=[cobro.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("usuarios:login"), r.url)

    def test_filtro_revisado_si_no_on_uno_y_valor_invalido(self):
        for n in range(120):
            self.cobro(revisado=n < 10)
        self.client.force_login(self.admin)

        def total(**q):
            return self.client.get(reverse(LISTA), q).context["pagina"].paginator.count

        self.assertEqual(total(), 120)
        self.assertEqual(total(revisado="si"), 10)
        self.assertEqual(total(revisado="no"), 110)
        self.assertEqual(total(revisado="on"), 10)
        self.assertEqual(total(revisado="1"), 10)
        self.assertEqual(total(revisado="xyz"), 120)


class LoteTest(Base017):
    def post(self, pks, usuario=None, **datos):
        self.client.force_login(usuario or self.admin)
        datos = {"motivo": "datos_erroneos", **datos}
        return self.client.post(reverse("cobros:cancelar_lote"), {"pk": pks, **datos}, follow=True)

    def armar(self):
        a, b = self.cobro(), self.cobro()
        fin = self.cobro(estado="finalizado")
        facturado = self.cobro()
        factura_prueba(
            facturado,
            estado="emitido",
            numero=1,
            cae="70000000000001",
            cae_vence=date(2026, 10, 30),
            fecha=date(2026, 10, 20),
            neto="100.00",
            iva="0.00",
            total="100.00",
        )
        return a, b, fin, facturado

    def test_cancela_los_pendientes_y_omite_finalizado_y_facturado(self):
        a, b, fin, facturado = self.armar()
        self.assertEqual(
            servicios.cancelar_cobros(
                [a.pk, b.pk, fin.pk, facturado.pk], usuario=self.admin, motivo="datos_erroneos"
            ),
            ([a.pk, b.pk], [fin.pk, facturado.pk]),
        )
        for c in (a, b, fin, facturado):
            c.refresh_from_db()
        self.assertEqual(
            [c.estado for c in (a, b, fin, facturado)],
            ["cancelado"] * 2 + ["finalizado", "pendiente"],
        )
        self.assertEqual((eventos(a), eventos(b), eventos(fin)), (1, 1, 0))

    def test_avisos_de_la_vista(self):
        a, b, fin, facturado = self.armar()
        r = self.post([a.pk, b.pk, fin.pk, facturado.pk])
        textos = [m.message for m in r.context["messages"]]
        self.assertIn("Cancelados: 2. Omitidos: 2.", textos)
        self.assertIn(f"Cobros omitidos: N.º {fin.pk}, N.º {facturado.pk}.", textos)

    def test_el_cupo_sube_en_el_monto_cancelado(self):
        from sistema.asignacion.servicios import lista_con_cupo

        a, b = self.cobro(monto=D("1000.00")), self.cobro(monto=D("2500.00"))
        antes = lista_con_cupo()[0].cupo_mensual
        servicios.cancelar_cobros([a.pk, b.pk], usuario=self.admin, motivo="no_pago")
        self.assertEqual(lista_con_cupo()[0].cupo_mensual, antes + D("3500.00"))

    def test_motivo_vacio_o_invalido_cancela_0(self):
        a = self.cobro()
        for motivo in ("", "inventado"):
            r = self.post([a.pk], motivo=motivo)
            self.assertIn(
                "Elegí un motivo de cancelación.", [m.message for m in r.context["messages"]]
            )
        a.refresh_from_db()
        self.assertEqual(a.estado, "pendiente")

    def test_otro_sin_detalle_cancela_0_y_con_detalle_cancela(self):
        a = self.cobro()
        self.post([a.pk], motivo="otro", detalle="  ")
        a.refresh_from_db()
        self.assertEqual(a.estado, "pendiente")
        self.post([a.pk], motivo="otro", detalle="Detalle de prueba")
        a.refresh_from_db()
        self.assertEqual((a.estado, a.detalle_cancelacion), ("cancelado", "Detalle de prueba"))

    def test_sin_elegidos_avisa(self):
        r = self.post([])
        self.assertIn("Elegí al menos un cobro.", [m.message for m in r.context["messages"]])

    def test_roles(self):
        a = self.cobro()
        self.assertEqual(self.post([a.pk], self.ana).status_code, 403)
        self.assertEqual(self.post([a.pk], self.prof).status_code, 403)
        self.client.logout()
        r = self.client.post(reverse("cobros:cancelar_lote"), {"pk": [a.pk], "motivo": "no_pago"})
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertEqual(a.estado, "pendiente")

    def test_servicio_sin_admin_levanta_permiso(self):
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            servicios.cancelar_cobros([], usuario=self.ana, motivo="no_pago")


class ListaAdminTest(Base017):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def lista(self, **q):
        return self.client.get(reverse(LISTA), q)

    def subtotal(self, **q):
        m = re.search(r"Subtotal sin cancelados: ([^<\s]+)", self.lista(**q).content.decode())
        return m.group(1) if m else None

    def test_subtotal_excluye_cancelados(self):
        self.cobro(monto=D("100000.00"))
        self.cobro(monto=D("50000.50"))
        self.cobro(monto=D("30000.00"), estado="cancelado")
        self.assertEqual(self.subtotal(), "$150.000,50")

    def test_subtotal_solo_cancelados_es_cero(self):
        self.cobro(monto=D("30000.00"), estado="cancelado")
        self.assertEqual(self.subtotal(), "$0,00")

    def test_subtotal_de_un_centavo(self):
        self.cobro(monto=D("0.01"))
        self.assertEqual(self.subtotal(), "$0,01")

    def test_subtotal_suma_las_tres_paginas(self):
        for _ in range(120):
            self.cobro(monto=D("10.00"))
        r = self.lista(page=3)
        self.assertEqual(len(r.context["pagina"]), 20)
        self.assertEqual(self.subtotal(page=3), "$1.200,00")

    def test_subtotal_con_filtro_profesional_suma_el_cobro_una_vez(self):
        Profesional.objects.create(id_dentalink=77, nombre="Profesional Ejemplo")
        c = self.cobro(monto=D("2000.00"))
        for n in (1, 2):
            ItemCobro.objects.create(
                cobro=c, numero=n, id_prestacion=9000 + n, monto=D("1000.00"), id_profesional=77
            )
        self.cobro(monto=D("500.00"))
        pro = Profesional.objects.get(id_dentalink=77)
        self.assertEqual(self.subtotal(profesional=pro.pk), "$2.000,00")

    def test_orden_por_monto_ascendente_y_descendente(self):
        c1 = self.cobro(monto=D("300.00"))
        c2 = self.cobro(monto=D("100.00"))
        c3 = self.cobro(monto=D("200.00"))
        asc = [c.pk for c in self.lista(orden="monto").context["pagina"]]
        desc = [c.pk for c in self.lista(orden="-monto").context["pagina"]]
        self.assertEqual(asc, [c2.pk, c3.pk, c1.pk])
        self.assertEqual(desc, [c1.pk, c3.pk, c2.pk])

    def test_orden_fuera_de_la_lista_usa_el_orden_por_defecto(self):
        c1, c2 = self.cobro(), self.cobro()
        r = self.lista(orden="cuenta__destinatario__cbu")
        self.assertEqual([c.pk for c in r.context["pagina"]], [c2.pk, c1.pk])
        self.assertEqual(r.context["orden"], "")

    def test_columnas_creado_por_y_abrir_solo_para_el_administrador(self):
        c = self.cobro(comprobante="comprobantes/prueba.pdf", estado="finalizado")
        r = self.lista()
        self.assertContains(r, "Creado por")
        self.client.force_login(self.ana)
        r = self.lista()
        self.assertNotContains(r, reverse("cobros:comprobante", args=[c.pk]))
        self.assertNotContains(r, "Creado por")

    def test_busqueda_en_observaciones_y_destinatario(self):
        self.cobro(observaciones="transfirió otra persona")
        self.cobro()
        self.assertEqual(self.lista(q="OTRA").context["pagina"].paginator.count, 1)
        # el destinatario de la prueba se llama "Dra Prueba"
        self.assertEqual(self.lista(q="dra prueba").context["pagina"].paginator.count, 2)

    def test_operador_no_busca_en_observaciones(self):
        self.cobro(observaciones="transfirió otra persona")
        self.client.force_login(self.ana)
        self.assertEqual(self.lista(q="OTRA").context["pagina"].paginator.count, 0)
