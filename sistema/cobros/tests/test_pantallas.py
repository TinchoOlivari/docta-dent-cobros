from datetime import datetime

from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba, con_elegidas, post_nuevo
from sistema.cobros.tests.test_comprobante import imagen
from decimal import Decimal

from sistema.cobros.tests.test_comprobantes_n import BaseComprobantes
from sistema.cobros.tests.test_efectivo import ARG, BaseEfectivo, item
from sistema.cobros.tests.test_pos import BasePos
from sistema.cobros.tests.test_transferencia import BaseTransferencia

D = Decimal

NUEVO = reverse("cobros:nuevo")


def datos_efectivo(**kw):
    return con_elegidas({"metodo": "efectivo", "monto_1": "40000,00", "monto_2": "25000,00", **kw})


class AltaPantallaTest(BaseEfectivo):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)

    def test_efectivo_con_dos_items_redirige_al_detalle(self):
        r = self.client.post(f"{NUEVO}?paciente=501", datos_efectivo())
        cobro = Cobro.objects.get()
        self.assertRedirects(r, reverse("cobros:detalle", args=[cobro.pk]))
        self.assertEqual(cobro.items.count(), 2)
        self.assertEqual(str(cobro.monto), "65000.00")

    def test_monto_con_coma_decimal_se_acepta(self):
        self.client.post(f"{NUEVO}?paciente=501", datos_efectivo(monto_1="", monto_2="24999,50"))
        self.assertEqual(str(Cobro.objects.get().monto), "24999.50")

    def test_monto_un_centavo_arriba_del_saldo_vuelve_al_form_con_el_error(self):
        r = self.client.post(f"{NUEVO}?paciente=501", datos_efectivo(monto_1="40000,01"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "supera el saldo disponible de $40.000,00")
        self.assertEqual(Cobro.objects.count(), 0)

    def test_sin_montos_vuelve_al_form_con_el_error(self):
        r = self.client.post(f"{NUEVO}?paciente=501", datos_efectivo(monto_1="", monto_2=""))
        self.assertContains(r, "Elegí al menos una prestación.")

    def test_el_operador_no_ve_el_cobro_en_su_pantalla_pero_si_en_mis_cobros(self):
        self.client.post(f"{NUEVO}?paciente=501", datos_efectivo())
        cobro = Cobro.objects.get()
        r = self.client.get(reverse("usuarios:operador"))
        self.assertNotContains(r, reverse("cobros:detalle", args=[cobro.pk]))
        self.assertNotContains(r, f"Cobro {cobro.pk}")
        self.assertContains(r, reverse("cobros:lista"))
        self.assertContains(r, reverse("dentalink:pacientes"))
        r = self.client.get(reverse("cobros:lista"))
        self.assertContains(r, reverse("cobros:detalle", args=[cobro.pk]))

    def test_el_otro_operador_no_ve_el_detalle(self):
        self.client.post(f"{NUEVO}?paciente=501", datos_efectivo())
        luis = get_user_model().objects.create_user("luis", email="l@example.com", role="operator")
        self.client.force_login(luis)
        r = self.client.get(reverse("cobros:detalle", args=[Cobro.objects.get().pk]))
        self.assertEqual(r.status_code, 404)

    def test_profesional_no_entra_a_nuevo(self):
        drax = get_user_model().objects.create_user(
            "drax", email="d@example.com", role="professional"
        )
        self.client.force_login(drax)
        self.assertEqual(self.client.get(f"{NUEVO}?paciente=501").status_code, 403)


class PosPantallaTest(BasePos):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)

    def test_detalle_pos_para_el_operador_solo_muestra_la_terminal(self):
        self.pos()
        cobro = Cobro.objects.get()
        r = self.client.get(reverse("cobros:detalle", args=[cobro.pk]))
        self.assertContains(r, "Point Smart #1")
        self.assertNotContains(r, "Rodrigo")
        self.assertNotContains(r, str(self.cuenta))

    def test_form_ofrece_solo_nombres_de_terminales(self):
        r = self.client.get(f"{NUEVO}?paciente=501")
        self.assertContains(r, "Point Smart #1")
        self.assertNotContains(r, "Rodrigo")

    def test_alta_pos_por_pantalla(self):
        r = self.client.post(
            f"{NUEVO}?paciente=501",
            con_elegidas(
                {
                    "metodo": "debito",
                    "monto_1": "30000,00",
                    "terminal": self.smart.pk,
                    "monto_neto": "29100,50",
                    "numero_operacion": "88213",
                    "banco_emisor": self.banco.pk,
                    "ultimos_4": "4321",
                    "comprobante": imagen(),
                }
            ),
        )
        cobro = Cobro.objects.get()
        self.assertRedirects(r, reverse("cobros:detalle", args=[cobro.pk]))
        self.assertEqual(cobro.estado, "finalizado")


class TransferenciaPantallaTest(BaseTransferencia):
    def setUp(self):
        super().setUp()
        self.cuenta.cbu = "0000000000000000000001"
        self.cuenta.save()
        self.client.force_login(self.ana)

    def alta_pantalla(self):
        self.client.post(
            f"{NUEVO}?paciente=501",
            con_elegidas({"metodo": "transferencia", "monto_1": "40000,00", "monto_2": "10000,00"}),
        )
        return Cobro.objects.get()

    def test_alta_muestra_alias_y_cbu_en_el_detalle(self):
        cobro = self.alta_pantalla()
        self.assertEqual(cobro.estado, "pendiente")
        r = self.client.get(reverse("cobros:detalle", args=[cobro.pk]))
        self.assertContains(r, "prueba.uno")
        self.assertContains(r, "0000000000000000000001")

    def test_adjuntar_png_finaliza_el_cobro(self):
        cobro = self.alta_pantalla()
        r = self.client.post(reverse("cobros:adjuntar", args=[cobro.pk]), {"comprobante": imagen()})
        self.assertRedirects(r, reverse("cobros:detalle", args=[cobro.pk]))
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")

    def test_adjuntar_sin_archivo_avisa_y_sigue_pendiente(self):
        cobro = self.alta_pantalla()
        r = self.client.post(reverse("cobros:adjuntar", args=[cobro.pk]), follow=True)
        self.assertContains(r, "Falta el comprobante.")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "pendiente")

    def test_cancelar_otro_sin_detalle_avisa_y_sigue_pendiente(self):
        cobro = self.alta_pantalla()
        r = self.client.post(
            reverse("cobros:cancelar", args=[cobro.pk]),
            {"motivo": "otro", "detalle": "", "estado_visto": "pendiente"},
        )
        self.assertContains(r, "Escribí el detalle del motivo.", status_code=400)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "pendiente")

    def test_cancelar_con_motivo_cancela(self):
        cobro = self.alta_pantalla()
        self.client.post(
            reverse("cobros:cancelar", args=[cobro.pk]),
            {"motivo": "no_pago", "detalle": "", "estado_visto": "pendiente"},
        )
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "cancelado")

    def test_operador_no_cancela_un_finalizado_403(self):
        cobro = self.alta_pantalla()
        self.client.post(reverse("cobros:adjuntar", args=[cobro.pk]), {"comprobante": imagen()})
        r = self.client.post(
            reverse("cobros:cancelar", args=[cobro.pk]),
            {"motivo": "no_pago", "estado_visto": "finalizado"},
        )
        self.assertEqual(r.status_code, 403)

    def test_adjuntar_y_cancelar_solo_aceptan_post(self):
        cobro = self.alta_pantalla()
        self.assertEqual(
            self.client.get(reverse("cobros:adjuntar", args=[cobro.pk])).status_code, 405
        )
        self.assertEqual(
            self.client.get(reverse("cobros:cancelar", args=[cobro.pk])).status_code, 405
        )

    def test_sin_dentalink_crea_el_cobro_con_la_marca(self):
        r = self.client.post(
            f"{NUEVO}?sin_dentalink=1",
            {"metodo": "transferencia", "nombre_paciente": "Ana Inventada", "monto": "1234,56"},
        )
        cobro = Cobro.objects.get()
        # 024/FR-014: la transferencia sigue en `cobros:nuevo` con su cuenta
        self.assertRedirects(r, f"{reverse('cobros:nuevo')}?cobro={cobro.pk}")
        self.assertTrue(cobro.faltan_items)
        self.assertEqual(str(cobro.monto), "1234.56")


class AdminPantallaTest(BaseTransferencia):
    def setUp(self):
        super().setUp()
        self.cuenta.cbu = "0000000000000000000001"
        self.cuenta.save()
        self.client.force_login(self.admin)

    def final(self):
        cobro = self.alta()
        servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        cobro.refresh_from_db()
        return cobro

    def numeros(self, **filtros):
        r = self.client.get(reverse("cobros:lista"), filtros)
        return {c.pk for c in r.context["pagina"]}

    def test_filtros_de_la_lista_del_administrador(self):
        base = {"cuenta": self.cuenta, "monto": "1000.00", "creado_por": self.ana}
        comun = cobro_prueba(**base)
        exc = cobro_prueba(**base, excedente=True)
        revisado = cobro_prueba(**base, excedente=True, revisado=True)
        faltan = cobro_prueba(**base, faltan_items=True)
        cancelado = cobro_prueba(**base, estado="cancelado")
        self.assertEqual(self.numeros(), {c.pk for c in (comun, exc, revisado, faltan, cancelado)})
        self.assertEqual(self.numeros(excedente_sin_revisar="1"), {exc.pk})
        self.assertEqual(self.numeros(faltan_items="1"), {faltan.pk})
        self.assertEqual(self.numeros(estado="cancelado"), {cancelado.pk})
        self.assertEqual(self.numeros(estado="invalido"), self.numeros())

    def test_la_lista_muestra_el_destinatario_y_no_la_cuenta(self):
        # 018/FR-022: la columna "Cuenta" sale; entra "Destinatario".
        cobro = self.alta()
        r = self.client.get(reverse("cobros:lista"))
        self.assertContains(r, cobro.destinatario.nombre)
        self.assertNotContains(r, "0000000000000000000001")

    def test_el_operador_no_usa_los_filtros_del_administrador(self):
        base = {"cuenta": self.cuenta, "monto": "1000.00", "creado_por": self.ana}
        a = cobro_prueba(**base)
        b = cobro_prueba(**base, faltan_items=True)
        self.client.force_login(self.ana)
        self.assertEqual(self.numeros(faltan_items="1"), {a.pk, b.pk})

    def test_post_editar_cambia_la_fecha(self):
        cobro = self.final()
        r = self.client.post(
            reverse("cobros:editar", args=[cobro.pk]),
            {
                "fecha_0": "14/10/2026",
                "fecha_1": "11:30",
                "monto": "50000,00",
                "metodo": "transferencia",
                "cuenta": self.cuenta.pk,
                "estado_visto": "finalizado",
                **{f"monto_item_{i.pk}": str(i.monto).replace(".", ",") for i in cobro.items.all()},
            },
        )
        self.assertRedirects(r, reverse("cobros:detalle", args=[cobro.pk]))
        cobro.refresh_from_db()
        self.assertEqual(cobro.fecha, datetime(2026, 10, 14, 11, 30, tzinfo=ARG))

    def test_editar_es_solo_del_administrador(self):
        cobro = self.final()
        self.client.force_login(self.ana)
        self.assertEqual(
            self.client.get(reverse("cobros:editar", args=[cobro.pk])).status_code, 403
        )

    def test_post_revisado_marca_el_cobro(self):
        cobro = self.final()
        Cobro.objects.filter(pk=cobro.pk).update(excedente=True)
        url = reverse("cobros:revisado", args=[cobro.pk])
        self.client.post(url, {"comentario": "  "})
        cobro.refresh_from_db()
        self.assertFalse(cobro.revisado)
        self.client.post(url, {"comentario": "Está bien"})
        cobro.refresh_from_db()
        self.assertTrue(cobro.revisado)
        self.assertEqual(cobro.comentario_revision, "Está bien")

    def test_comprobante_devuelve_el_archivo_y_sin_comprobante_da_404(self):
        cobro = self.final()
        r = self.client.get(reverse("cobros:comprobante", args=[cobro.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content)[:2], b"\xff\xd8")
        sin = self.alta(monto="1000.00", items=[item(2, "1000.00")])
        self.assertEqual(
            self.client.get(reverse("cobros:comprobante", args=[sin.pk])).status_code, 404
        )

    def test_operador_abre_su_comprobante_pero_no_marca_revisado(self):
        cobro = self.final()
        self.client.force_login(self.ana)
        self.assertEqual(
            self.client.get(reverse("cobros:comprobante", args=[cobro.pk])).status_code, 200
        )  # 021/FR-012: el operador abre el comprobante de su cobro
        self.assertEqual(
            self.client.post(reverse("cobros:revisado", args=[cobro.pk])).status_code, 403
        )

    def test_detalle_del_operador_enlaza_comprobante_pero_no_historial(self):
        cobro = self.final()
        self.client.force_login(self.ana)
        r = self.client.get(reverse("cobros:detalle", args=[cobro.pk]))
        self.assertNotContains(r, "Historial")
        self.assertContains(r, reverse("cobros:comprobante", args=[cobro.pk]))


class CompletarItemsPantallaTest(BaseEfectivo):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)
        self.cobro = cobro_prueba(
            cuenta=self.efectivo,
            metodo="efectivo",
            estado="finalizado",
            monto="65000.00",
            faltan_items=True,
            creado_por=self.ana,
            nombre_paciente="Ana Inventada",
        )

    def test_get_sin_paciente_redirige_a_buscar_paciente(self):
        r = self.client.get(reverse("cobros:items", args=[self.cobro.pk]))
        self.assertRedirects(
            r,
            f"{reverse('dentalink:pacientes')}?cobro={self.cobro.pk}",
            fetch_redirect_response=False,
        )

    def test_post_con_items_que_suman_el_monto_quita_la_marca(self):
        url = f"{reverse('cobros:items', args=[self.cobro.pk])}?paciente=501"
        self.assertContains(self.client.get(url), "Prestación 1")
        r = self.client.post(url, post_nuevo({1: "40000,00", 2: "25000,00"}))
        self.assertRedirects(r, reverse("cobros:detalle", args=[self.cobro.pk]))
        self.cobro.refresh_from_db()
        self.assertFalse(self.cobro.faltan_items)
        self.assertEqual(self.cobro.items.count(), 2)

    def test_post_con_suma_distinta_un_centavo_vuelve_con_el_error(self):
        url = f"{reverse('cobros:items', args=[self.cobro.pk])}?paciente=501"
        r = self.client.post(url, post_nuevo({1: "40000,00", 2: "24999,99"}))
        self.assertContains(r, "Los ítems suman")
        self.cobro.refresh_from_db()
        self.assertTrue(self.cobro.faltan_items)

    def test_cobro_sin_la_marca_da_404(self):
        Cobro.objects.filter(pk=self.cobro.pk).update(faltan_items=False)
        r = self.client.get(reverse("cobros:items", args=[self.cobro.pk]))
        self.assertEqual(r.status_code, 404)


class ComprobantesReaperturaPantallaTest(BaseComprobantes):
    def setUp(self):
        super().setUp()
        self.cobro_fin = self.alta100()
        servicios.adjuntar_comprobante(self.cobro_fin, imagen(), usuario=self.ana)

    def detalle(self, cobro=None):
        return reverse("cobros:detalle", args=[(cobro or self.cobro_fin).pk])

    def otros(self, n):
        for _ in range(n):
            servicios.adjuntar_comprobante(
                self.cobro_fin, imagen(), usuario=self.admin, estado_visto="finalizado"
            )

    def test_admin_adjunta_otro_a_un_finalizado_y_lo_abre(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("cobros:adjuntar", args=[self.cobro_fin.pk]),
            {"comprobante": imagen(), "estado_visto": "finalizado"},
        )
        self.assertRedirects(r, self.detalle())
        otro = self.cobro_fin.otros_comprobantes.get()
        r = self.client.get(reverse("cobros:comprobante_otro", args=[otro.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), otro.archivo.read())
        r = self.client.get(reverse("cobros:comprobante_otro", args=[otro.pk + 999]))
        self.assertEqual(r.status_code, 404)

    def test_operador_ve_la_cantidad_y_no_los_enlaces_ni_el_form(self):
        self.otros(2)
        self.client.force_login(self.ana)
        r = self.client.get(self.detalle())
        self.assertContains(r, "Comprobantes (3)")
        self.assertNotContains(r, "/comprobantes/")
        self.assertNotContains(r, reverse("cobros:adjuntar", args=[self.cobro_fin.pk]))
        self.assertNotContains(r, reverse("cobros:reabrir", args=[self.cobro_fin.pk]))

    def test_operador_no_abre_un_comprobante_otro(self):
        self.otros(1)
        otro = self.cobro_fin.otros_comprobantes.get()
        self.client.force_login(self.ana)
        r = self.client.get(reverse("cobros:comprobante_otro", args=[otro.pk]))
        self.assertEqual(r.status_code, 403)

    def test_reabrir_con_motivo_y_sin_motivo(self):
        self.client.force_login(self.admin)
        url = reverse("cobros:reabrir", args=[self.cobro_fin.pk])
        r = self.client.post(url, {"motivo": " ", "estado_visto": "finalizado"}, follow=True)
        self.assertContains(r, "Escribí el motivo de la reapertura.")
        self.cobro_fin.refresh_from_db()
        self.assertEqual(self.cobro_fin.estado, "finalizado")
        r = self.client.post(
            url, {"motivo": "Falta pagar", "estado_visto": "finalizado"}, follow=True
        )
        self.assertContains(r, "Cobro reabierto.")
        self.cobro_fin.refresh_from_db()
        self.assertEqual(self.cobro_fin.estado, "pendiente")


class SinDentalinkTest(BaseTransferencia):
    ENLACE = "Cobrar sin conectar con Dentalink"
    SIN = "?sin_dentalink=1"

    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)
        self.pacientes = reverse("dentalink:pacientes")

    def test_alta_sin_dentalink_con_dentalink_activo_no_consulta_al_paciente(self):
        datos = {"metodo": "transferencia", "monto": "1000,00", "nombre_paciente": "Juan Paciente"}
        self.paciente.reset_mock()
        r = self.client.post(f"{NUEVO}{self.SIN}", datos)
        cobro = Cobro.objects.get()
        # 024/FR-014: la transferencia sigue en `cobros:nuevo` con su cuenta
        self.assertRedirects(r, f"{reverse('cobros:nuevo')}?cobro={cobro.pk}")
        self.assertTrue(cobro.faltan_items)
        self.assertIsNone(cobro.id_paciente_dentalink)
        self.paciente.assert_not_called()


class MenuNuevoCobroTest(BaseTransferencia):
    def test_operador_y_profesional_no_ven_el_menu_ni_el_panel_del_administrador(self):
        drax = get_user_model().objects.create_user(
            "drax", email="d@example.com", role="professional"
        )
        for usuario in (self.ana, drax):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse("usuarios:panel")).status_code, 403)
        self.client.force_login(self.ana)
        # el menú del operador tiene «Nuevo cobro» (menú lateral + versión sin JS), sin lo del admin
        r = self.client.get(reverse("usuarios:operador"))
        self.assertContains(r, "Nuevo cobro")
        self.assertNotContains(r, "Destinatarios")
        self.client.force_login(drax)
        self.assertNotContains(self.client.get(reverse("usuarios:profesional")), "Nuevo cobro")
