"""Permisos de 002 sobre cobros: FR-009 (SC-001), FR-011, FR-012, FR-014, US2 y Edge Cases."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.cobros import servicios
from sistema.cobros.models import Cobro, ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_efectivo import AHORA
from sistema.cobros.tests.test_pantallas import NUEVO, datos_efectivo
from sistema.cobros.tests.test_transferencia import BaseTransferencia
from sistema.core.permisos import filtro_cobros_visibles
from sistema.usuarios.models import Perfil
from sistema.usuarios.servicios import cambiar_activo

User = get_user_model()
LOGIN = reverse("usuarios:login")


class BasePermisos(BaseTransferencia):
    def setUp(self):
        super().setUp()
        self.luis = User.objects.create_user("luis", email="l@example.com", role="operator")
        self.drax = User.objects.create_user("drax", email="d@example.com", role="professional")

    def cobro(self, creador=None, minutos_atras=0, **kw):
        kw.setdefault("cuenta", self.cuenta)
        kw.setdefault("monto", Decimal("1000.00"))
        kw.setdefault("destinatario", self.dest)
        kw.setdefault("fecha", AHORA)  # el default usa el reloj real
        c = cobro_prueba(creado_por=creador or self.ana, **kw)
        # fecha_alta es auto_now_add: se fija con update, relativa al reloj fijo de la prueba
        Cobro.objects.filter(pk=c.pk).update(fecha_alta=AHORA - timedelta(minutes=minutos_atras))
        c.refresh_from_db()
        return c

    def como(self, usuario):
        self.client.force_login(usuario)
        return self.client

    def url(self, nombre, cobro):
        return reverse(f"cobros:{nombre}", args=[cobro.pk])

    def codigos(self, hacer, usuarios):
        """Código HTTP de `hacer(client)` para cada usuario; el estado se restaura entre usuarios."""
        return {u.username: hacer(self.como(u)).status_code for u in usuarios}


class TablaFR009Test(BasePermisos):
    """002/SC-001: cada fila de cobros de FR-009 con los tres roles."""

    def test_sin_sesion_todo_redirige_al_login(self):
        c = self.cobro()
        for nombre in ("detalle", "adjuntar", "cancelar", "items", "editar", "revisado"):
            r = self.client.post(self.url(nombre, c))
            self.assertEqual(r.status_code, 302, nombre)
            self.assertTrue(r["Location"].startswith(LOGIN), nombre)
        for destino in (reverse("cobros:lista"), NUEVO):
            self.assertTrue(self.client.get(destino)["Location"].startswith(LOGIN))

    def test_profesional_recibe_403_en_todas_las_pantallas_de_cobros(self):
        c = self.cobro()
        self.client.force_login(self.drax)
        for destino in (
            reverse("cobros:lista"),
            NUEVO,
            *(
                self.url(n, c)
                for n in (
                    "detalle",
                    "adjuntar",
                    "cancelar",
                    "items",
                    "editar",
                    "revisado",
                    "comprobante",
                )
            ),
            reverse("auditoria:historial", args=["cobros.cobro", c.pk]),
        ):
            self.assertEqual(self.client.post(destino).status_code, 403, destino)
            self.assertEqual(self.client.get(destino).status_code, 403, destino)

    def test_crear_admin_y_operador_si_profesional_no(self):
        r = self.como(self.admin).post(f"{NUEVO}?paciente=501", datos_efectivo(monto_2=""))
        self.assertEqual(r.status_code, 302)
        r = self.como(self.ana).post(f"{NUEVO}?paciente=501", datos_efectivo(monto_1=""))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            sorted(Cobro.objects.values_list("creado_por__username", flat=True)), ["admin", "ana"]
        )
        r = self.como(self.drax).post(f"{NUEVO}?paciente=501", datos_efectivo())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Cobro.objects.count(), 2)

    def test_ver_admin_todos_operador_propios_profesional_no(self):
        c = self.cobro()
        ajeno = self.cobro(creador=self.luis)
        lista = reverse("cobros:lista")
        r = self.como(self.admin).get(lista)
        self.assertContains(r, self.url("detalle", c))
        self.assertContains(r, self.url("detalle", ajeno))
        r = self.como(self.ana).get(lista)
        self.assertContains(r, self.url("detalle", c))
        self.assertNotContains(r, self.url("detalle", ajeno))
        self.assertEqual(self.como(self.admin).get(self.url("detalle", ajeno)).status_code, 200)
        self.assertEqual(self.como(self.ana).get(self.url("detalle", c)).status_code, 200)
        self.assertEqual(self.como(self.ana).get(self.url("detalle", ajeno)).status_code, 404)
        self.assertEqual(self.como(self.drax).get(lista).status_code, 403)

    def test_adjuntar_a_pendiente_admin_y_dueno_si_otro_operador_y_profesional_no(self):
        for usuario, esperado in ((self.admin, 302), (self.ana, 302), (self.luis, 404)):
            c = self.cobro()
            r = self.como(usuario).post(
                self.url("adjuntar", c), {"comprobante": imagen(), "estado_visto": "pendiente"}
            )
            c.refresh_from_db()
            self.assertEqual(r.status_code, esperado, usuario.username)
            self.assertEqual(c.estado, "finalizado" if esperado == 302 else "pendiente")
        c = self.cobro()
        r = self.como(self.drax).post(self.url("adjuntar", c), {"comprobante": imagen()})
        self.assertEqual(r.status_code, 403)

    def test_cancelar_pendiente_admin_y_dueno_si_otro_operador_y_profesional_no(self):
        datos = {"motivo": "otro_medio", "estado_visto": "pendiente"}
        for usuario, esperado in ((self.admin, 302), (self.ana, 302), (self.luis, 404)):
            c = self.cobro()
            r = self.como(usuario).post(self.url("cancelar", c), datos)
            c.refresh_from_db()
            self.assertEqual(r.status_code, esperado, usuario.username)
            self.assertEqual(c.estado, "cancelado" if esperado == 302 else "pendiente")
        c = self.cobro()
        self.assertEqual(self.como(self.drax).post(self.url("cancelar", c), datos).status_code, 403)

    def test_cancelar_finalizado_solo_admin(self):
        datos = {"motivo": "otro_medio", "estado_visto": "finalizado"}
        c = self.cobro(estado="finalizado")
        for usuario in (self.ana, self.drax):
            r = self.como(usuario).post(self.url("cancelar", c), datos)
            self.assertEqual(r.status_code, 403, usuario.username)
            c.refresh_from_db()
            self.assertEqual(c.estado, "finalizado")
        self.como(self.admin).post(self.url("cancelar", c), datos)
        c.refresh_from_db()
        self.assertEqual(c.estado, "cancelado")

    def test_editar_finalizado_y_cambiar_fecha_solo_admin(self):
        c = self.cobro(estado="finalizado")
        url = self.url("editar", c)
        self.assertEqual(self.como(self.admin).get(url).status_code, 200)
        for usuario in (self.ana, self.drax):
            self.como(usuario)
            self.assertEqual(self.client.get(url).status_code, 403, usuario.username)
            r = self.client.post(url, {"fecha": "2026-10-01 10:00"})
            self.assertEqual(r.status_code, 403, usuario.username)
        c.refresh_from_db()
        self.assertEqual(c.fecha, AHORA)

    def test_agregar_items_con_faltan_items_admin_y_dueno_si_otros_no(self):
        c = self.cobro(faltan_items=True)
        url = self.url("items", c)
        for usuario, esperado in (
            (self.admin, 302),
            (self.ana, 302),  # sin ?paciente= redirige a buscar paciente: pasó el permiso
            (self.luis, 404),
            (self.drax, 403),
        ):
            self.assertEqual(self.como(usuario).get(url).status_code, esperado, usuario.username)

    def test_items_de_un_cobro_sin_la_marca_da_404_incluso_al_dueno(self):
        c = self.cobro(faltan_items=False)
        self.assertEqual(self.como(self.ana).get(self.url("items", c)).status_code, 404)

    def test_marcar_revisado_solo_admin(self):
        c = self.cobro(estado="finalizado", excedente=True)
        url = self.url("revisado", c)
        for usuario in (self.ana, self.drax):
            r = self.como(usuario).post(url, {"comentario": "ok"})
            self.assertEqual(r.status_code, 403, usuario.username)
        c.refresh_from_db()
        self.assertFalse(c.revisado)
        self.como(self.admin).post(url, {"comentario": "ok"})
        c.refresh_from_db()
        self.assertTrue(c.revisado)

    def test_abrir_comprobante_admin_y_operador_visible_no_profesional(self):
        c = self.cobro()
        servicios.adjuntar_comprobante(c, imagen(), usuario=self.ana)
        url = self.url("comprobante", c)
        self.assertEqual(self.como(self.admin).get(url).status_code, 200)
        self.assertEqual(self.como(self.ana).get(url).status_code, 200)  # 021/FR-012
        self.assertEqual(self.como(self.drax).get(url).status_code, 403)

    def test_ver_historial_del_cobro_solo_admin(self):
        c = self.cobro()
        url = reverse("auditoria:historial", args=["cobros.cobro", c.pk])
        self.assertEqual(self.como(self.admin).get(url).status_code, 200)
        self.assertEqual(self.como(self.ana).get(url).status_code, 403)
        self.assertEqual(self.como(self.drax).get(url).status_code, 403)

    def test_profesional_ve_sus_items_sin_destinatario_ni_cuenta(self):
        """002/FR-012: fecha, paciente, tratamiento, prestación, monto, método y estado."""
        Perfil.objects.create(
            usuario=self.drax, id_profesional_dentalink=12, nombre_profesional_dentalink="Dr. D"
        )
        c = self.cobro(nombre_paciente="Pablo Paciente")
        ItemCobro.objects.create(
            cobro=c,
            numero=1,
            monto=Decimal("1000.00"),
            id_profesional=12,
            nombre_profesional="Dr. D",
            nombre_tratamiento="Ortodoncia",
            nombre_prestacion="Control",
        )
        r = self.como(self.drax).get(reverse("usuarios:profesional"))
        for texto in ("Pablo Paciente", "Ortodoncia", "Control", "$1.000,00", "Transferencia"):
            self.assertContains(r, texto)
        self.assertContains(r, "Pendiente")
        self.assertNotContains(r, "Dra Prueba")
        self.assertNotContains(r, "prueba.uno")


class OperadorUS2Test(BasePermisos):
    """002/US2 y FR-011: ventana de 15 días contada hacia atrás desde la consulta."""

    DIAS_14_23H = 14 * 24 * 60 + 23 * 60
    DIAS_15 = 15 * 24 * 60

    def test_filtro_en_los_limites_con_filas_reales(self):
        dentro = self.cobro(minutos_atras=self.DIAS_14_23H)
        justo = self.cobro(minutos_atras=self.DIAS_15)
        fuera = self.cobro(minutos_atras=self.DIAS_15 + 1)
        cero = self.cobro(minutos_atras=0)
        visibles = set(
            Cobro.objects.filter(filtro_cobros_visibles(self.ana, AHORA)).values_list(
                "pk", flat=True
            )
        )
        self.assertEqual(visibles, {dentro.pk, justo.pk, cero.pk})
        self.assertNotIn(fuera.pk, visibles)

    def test_us2_1_y_2_lista_y_detalle_en_los_limites(self):
        dentro = self.cobro(minutos_atras=self.DIAS_14_23H)
        justo = self.cobro(minutos_atras=self.DIAS_15)
        fuera = self.cobro(minutos_atras=self.DIAS_15 + 1)
        self.como(self.ana)
        lista = self.client.get(reverse("cobros:lista"))
        self.assertContains(lista, self.url("detalle", dentro))
        self.assertContains(lista, self.url("detalle", justo))
        self.assertNotContains(lista, self.url("detalle", fuera))
        for c, esperado in ((dentro, 200), (justo, 200), (fuera, 404)):
            self.assertEqual(self.client.get(self.url("detalle", c)).status_code, esperado)

    def test_fuera_de_la_ventana_adjuntar_cancelar_e_items_dan_404(self):
        fuera = self.cobro(minutos_atras=self.DIAS_15 + 1, faltan_items=True)
        self.como(self.ana)
        self.assertEqual(
            self.client.post(self.url("adjuntar", fuera), {"comprobante": imagen()}).status_code,
            404,
        )
        datos = {"motivo": "otro_medio", "estado_visto": "pendiente"}
        self.assertEqual(self.client.post(self.url("cancelar", fuera), datos).status_code, 404)
        self.assertEqual(self.client.get(self.url("items", fuera)).status_code, 404)
        fuera.refresh_from_db()
        self.assertEqual(fuera.estado, "pendiente")
        # el administrador no tiene ventana
        self.assertEqual(self.como(self.admin).get(self.url("detalle", fuera)).status_code, 200)

    def test_us2_3_5_8_cobro_de_luis_da_404_a_ana(self):
        c = self.cobro(creador=self.luis)
        self.como(self.ana)
        self.assertNotContains(self.client.get(reverse("cobros:lista")), self.url("detalle", c))
        self.assertEqual(self.client.get(self.url("detalle", c)).status_code, 404)
        datos = {"motivo": "otro_medio", "estado_visto": "pendiente"}
        self.assertEqual(self.client.post(self.url("cancelar", c), datos).status_code, 404)
        r = self.client.post(self.url("adjuntar", c), {"comprobante": imagen()})
        self.assertEqual(r.status_code, 404)
        c.refresh_from_db()
        self.assertEqual(c.estado, "pendiente")

    def test_us2_4_ana_cancela_su_pendiente_con_el_motivo(self):
        c = self.cobro()
        datos = {"motivo": "no_pago", "estado_visto": "pendiente"}
        self.como(self.ana).post(self.url("cancelar", c), datos)
        c.refresh_from_db()
        self.assertEqual(c.estado, "cancelado")
        self.assertEqual(c.get_motivo_cancelacion_display(), "El paciente no pagó")

    def test_us2_6_finalizado_propio_da_403_en_editar_y_cancelar(self):
        c = self.cobro(estado="finalizado")
        self.como(self.ana)
        self.assertEqual(self.client.get(self.url("editar", c)).status_code, 403)
        datos = {"motivo": "otro_medio", "estado_visto": "finalizado"}
        self.assertEqual(self.client.post(self.url("cancelar", c), datos).status_code, 403)
        c.refresh_from_db()
        self.assertEqual(c.estado, "finalizado")

    def test_us2_7_ana_adjunta_a_su_pendiente_y_queda_finalizado(self):
        c = self.cobro()
        self.como(self.ana).post(self.url("adjuntar", c), {"comprobante": imagen()})
        c.refresh_from_db()
        self.assertEqual(c.estado, "finalizado")


class EdgeYFr014Test(BasePermisos):
    def test_pendiente_de_20_dias_no_aparece_pero_sigue_reservando_cupo(self):
        from sistema.asignacion.servicios import lista_con_cupo

        c = self.cobro(minutos_atras=20 * 24 * 60, monto=Decimal("60000.00"))
        r = self.como(self.ana).get(reverse("cobros:lista"))
        self.assertNotContains(r, self.url("detalle", c))
        self.assertEqual(self.client.get(self.url("detalle", c)).status_code, 404)
        fila = next(f for f in lista_con_cupo(AHORA) if f.pk == self.cuenta.pk)
        self.assertEqual(fila.reservado_mes, Decimal("60000.00"))

    def test_us4_3_desactivar_a_ana_deja_sus_30_cobros_con_ana(self):
        for _ in range(30):
            self.cobro(monto=Decimal("1.00"))
        cambiar_activo(self.ana, False, usuario=self.admin)
        self.assertEqual(Cobro.objects.filter(creado_por=self.ana).count(), 30)

    def test_fr_014_lista_muestra_alias_y_cbu_y_destinatarios_da_403(self):
        self.cuenta.cbu = "0000000000000000000001"
        self.cuenta.save()
        self.cobro()
        self.como(self.ana)
        r = self.client.get(reverse("cobros:lista"))
        self.assertContains(r, "prueba.uno")
        self.assertContains(r, "0000000000000000000001")
        self.assertEqual(self.client.get(reverse("destinatarios:lista")).status_code, 403)
        self.assertEqual(self.client.get("/sistema/destinatarios/").status_code, 403)


class FilasFinalizadoTest(BasePermisos):
    """002/FR-009: «Adjuntar un comprobante a un cobro Finalizado» y «Reabrir un cobro Finalizado»."""

    def setUp(self):
        super().setUp()
        self.cobro_fin = self.alta()
        servicios.adjuntar_comprobante(self.cobro_fin, imagen(), usuario=self.ana)

    def adjuntar(self, usuario):
        return self.como(usuario).post(
            self.url("adjuntar", self.cobro_fin),
            {
                "comprobante": imagen(),
                "estado_visto": self.cobro_fin.__class__.objects.get(pk=self.cobro_fin.pk).estado,
            },
            follow=True,
        )

    def reabrir(self, usuario):
        return self.como(usuario).post(
            self.url("reabrir", self.cobro_fin), {"motivo": "Falta", "estado_visto": "finalizado"}
        )

    def test_administrador_si(self):
        self.adjuntar(self.admin)
        self.assertEqual(self.cobro_fin.otros_comprobantes.count(), 1)
        self.assertEqual(self.reabrir(self.admin).status_code, 302)
        self.cobro_fin.refresh_from_db()
        self.assertEqual(self.cobro_fin.estado, "pendiente")

    def test_operador_no(self):
        self.assertContains(self.adjuntar(self.ana), "El cobro ya está Finalizado.")
        self.assertEqual(self.cobro_fin.otros_comprobantes.count(), 0)
        self.assertEqual(self.reabrir(self.ana).status_code, 403)
        self.cobro_fin.refresh_from_db()
        self.assertEqual(self.cobro_fin.estado, "finalizado")

    def test_profesional_403_en_ambas(self):
        self.assertEqual(self.adjuntar(self.drax).status_code, 403)
        self.assertEqual(self.reabrir(self.drax).status_code, 403)
        self.assertEqual(self.cobro_fin.otros_comprobantes.count(), 0)

    def test_sin_sesion_va_al_login(self):
        for nombre in ("reabrir", "comprobante_otro"):
            destino = (
                reverse("cobros:comprobante_otro", args=[1])
                if nombre == "comprobante_otro"
                else None
            )
            r = self.client.post(destino or self.url(nombre, self.cobro_fin))
            self.assertEqual(r.status_code, 302, nombre)
            self.assertTrue(r["Location"].startswith(LOGIN), nombre)

    def reabierto_hace(self, **tiempo):
        self.como(self.admin).post(
            self.url("reabrir", self.cobro_fin), {"motivo": "Falta", "estado_visto": "finalizado"}
        )
        Cobro.objects.filter(pk=self.cobro_fin.pk).update(fecha_alta=AHORA - timedelta(**tiempo))

    def test_reabierto_a_14_dias_23_horas_ana_adjunta_y_finaliza(self):
        self.reabierto_hace(days=14, hours=23)
        r = self.adjuntar(self.ana)
        self.assertEqual(r.status_code, 200)
        self.cobro_fin.refresh_from_db()
        self.assertEqual(self.cobro_fin.estado, "finalizado")

    def test_reabierto_a_15_dias_1_minuto_ana_recibe_404_y_el_admin_finaliza(self):
        self.reabierto_hace(days=15, minutes=1)
        self.assertEqual(self.adjuntar(self.ana).status_code, 404)
        self.cobro_fin.refresh_from_db()
        self.assertEqual(self.cobro_fin.estado, "pendiente")
        self.adjuntar(self.admin)
        self.cobro_fin.refresh_from_db()
        self.assertEqual(self.cobro_fin.estado, "finalizado")
