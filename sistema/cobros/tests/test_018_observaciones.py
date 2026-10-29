"""018 FR-015/FR-016: observaciones desde el detalle, por rol y con casos límite."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from sistema.auditoria.servicios import historial_de
from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.cobros.tests.test_017_operador import Base017


def eventos(cobro):
    return historial_de(cobro).filter(tipo="cambio").count()


class ObservacionesBase(Base017):
    def post(self, cobro, texto="Pagó la madre", usuario=None, **extra):
        self.client.force_login(usuario or self.ana)
        return self.client.post(
            reverse("cobros:observaciones", args=[cobro.pk]), {"observaciones": texto}, **extra
        )

    def mensajes(self, r):
        self.client.force_login(self.ana)
        return [str(m) for m in r.wsgi_request._messages]


class ServicioTest(ObservacionesBase):
    def test_operador_en_su_cobro_finalizado_deja_un_evento_con_antes_y_despues(self):
        c = self.cobro(estado="finalizado")
        servicios.cambiar_observaciones(c, usuario=self.ana, observaciones="Pagó la madre")
        c.refresh_from_db()
        self.assertEqual(c.observaciones, "Pagó la madre")
        self.assertEqual(eventos(c), 1)
        cambio = historial_de(c).filter(tipo="cambio").get().cambios[0]
        self.assertEqual(
            (cambio["campo"], cambio["anterior"], cambio["nuevo"]),
            ("observaciones", "", "Pagó la madre"),
        )

    def test_mismo_texto_no_deja_evento(self):
        c = self.cobro(observaciones="igual")
        servicios.cambiar_observaciones(c, usuario=self.ana, observaciones="igual")
        servicios.cambiar_observaciones(c, usuario=self.ana, observaciones="  igual  ")
        self.assertEqual(eventos(c), 0)

    def test_se_quitan_los_espacios_de_los_costados(self):
        c = self.cobro()
        servicios.cambiar_observaciones(c, usuario=self.ana, observaciones="  hola  ")
        c.refresh_from_db()
        self.assertEqual(c.observaciones, "hola")

    def test_vaciar_las_observaciones_deja_un_evento(self):
        c = self.cobro(observaciones="algo")
        servicios.cambiar_observaciones(c, usuario=self.ana, observaciones="   ")
        c.refresh_from_db()
        self.assertEqual((c.observaciones, eventos(c)), ("", 1))

    def test_operador_en_su_cobro_cancelado_es_error_y_no_cambia(self):
        c = self.cobro(estado="cancelado", observaciones="antes")
        with self.assertRaisesMessage(ValidationError, "El cobro ya está Cancelado."):
            servicios.cambiar_observaciones(c, usuario=self.ana, observaciones="nuevo")
        c.refresh_from_db()
        self.assertEqual((c.observaciones, eventos(c)), ("antes", 0))

    def test_administrador_guarda_en_un_cobro_cancelado(self):
        c = self.cobro(estado="cancelado")
        servicios.cambiar_observaciones(c, usuario=self.admin, observaciones="nota")
        c.refresh_from_db()
        self.assertEqual((c.observaciones, eventos(c)), ("nota", 1))

    def test_operador_en_un_cobro_ajeno_y_profesional_son_permission_denied(self):
        otra = get_user_model().objects.create_user("otra", email="o@example.com", role="operator")
        c = self.cobro(otra)
        for usuario in (self.ana, self.prof):
            with self.subTest(usuario=usuario.username), self.assertRaises(PermissionDenied):
                servicios.cambiar_observaciones(c, usuario=usuario, observaciones="x")
        self.assertEqual(eventos(c), 0)

    def test_no_toca_fila_arca_ni_dentalink(self):
        c = self.cobro(estado="finalizado")
        with (
            mock.patch.object(servicios, "fila") as fila,
            mock.patch.object(servicios, "arca") as arca,
            mock.patch.object(servicios, "dentalink") as dentalink,
        ):
            servicios.cambiar_observaciones(c, usuario=self.ana, observaciones="solo texto")
        self.assertEqual(fila.mock_calls + arca.mock_calls + dentalink.mock_calls, [])


class VistaTest(ObservacionesBase):
    def test_guardar_redirige_al_detalle_con_el_aviso(self):
        c = self.cobro()
        r = self.post(c, follow=True)
        self.assertRedirects(r, reverse("cobros:detalle", args=[c.pk]))
        self.assertContains(r, "Observaciones guardadas.")
        c.refresh_from_db()
        self.assertEqual(c.observaciones, "Pagó la madre")

    def test_cancelado_propio_un_post_da_error_y_no_cambia(self):
        c = self.cobro(estado="cancelado")
        r = self.post(c, follow=True)
        self.assertContains(r, "El cobro ya está Cancelado.")
        c.refresh_from_db()
        self.assertEqual((c.observaciones, eventos(c)), ("", 0))

    def test_cobro_de_otra_operadora_es_404(self):
        otra = get_user_model().objects.create_user("otra", email="o@example.com", role="operator")
        c = self.cobro(otra)
        self.assertEqual(self.post(c).status_code, 404)

    def test_limite_de_15_dias_14_dias_23_horas_guarda_y_15_dias_1_hora_es_404(self):
        c = self.cobro()
        Cobro.objects.filter(pk=c.pk).update(
            fecha_alta=timezone.now() - timedelta(days=14, hours=23)
        )
        self.assertEqual(self.post(c).status_code, 302)
        Cobro.objects.filter(pk=c.pk).update(
            fecha_alta=timezone.now() - timedelta(days=15, hours=1)
        )
        self.assertEqual(self.post(c, "otro").status_code, 404)

    def test_profesional_es_403(self):
        c = self.cobro()
        self.assertEqual(self.post(c, usuario=self.prof).status_code, 403)

    def test_sin_sesion_va_al_login(self):
        c = self.cobro()
        r = self.client.post(reverse("cobros:observaciones", args=[c.pk]), {"observaciones": "x"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_get_no_esta_permitido(self):
        c = self.cobro()
        self.client.force_login(self.ana)
        r = self.client.get(reverse("cobros:observaciones", args=[c.pk]))
        self.assertEqual(r.status_code, 405)

    def test_administrador_guarda_en_cancelado(self):
        c = self.cobro(estado="cancelado")
        self.post(c, "  hola  ", usuario=self.admin)
        c.refresh_from_db()
        self.assertEqual(c.observaciones, "hola")

    def test_el_admin_puede_cambiar_un_cobro_de_cualquier_operadora(self):
        c = self.cobro()
        self.post(c, "nota", usuario=self.admin)
        c.refresh_from_db()
        self.assertEqual(c.observaciones, "nota")


class DetalleTest(ObservacionesBase):
    def detalle(self, c, usuario=None):
        return self.get_detalle(c, usuario)

    def test_el_servidor_sigue_aceptando_observaciones_al_adjuntar(self):
        from sistema.cobros.tests.test_comprobante import imagen

        c = self.cobro()
        self.client.force_login(self.ana)
        self.client.post(
            reverse("cobros:adjuntar", args=[c.pk]),
            {"comprobante": imagen(), "estado_visto": "pendiente", "observaciones": "al adjuntar"},
        )
        c.refresh_from_db()
        self.assertEqual(c.observaciones, "al adjuntar")

    def test_a11_la_ven_los_dos_roles_y_a12_solo_el_administrador(self):
        c = self.cobro(faltan_items=True, excedente=True)
        admin, operador = self.detalle(c, self.admin), self.detalle(c, self.ana)
        for r in (admin, operador):
            self.assertContains(
                r, "Sin vincular a Dentalink", count=2
            )  # la marca y el aria-label de la ayuda
            self.assertContains(r, "El cobro no tiene prestaciones de Dentalink y no se envía.")
            self.assertContains(r, "Vincular con Dentalink")
            self.assertNotContains(r, "Faltan ítems")
            self.assertNotContains(r, "Completar ítems")
        self.assertContains(admin, "Este cobro hizo pasar el tope mensual de la cuenta.")
        self.assertContains(admin, 'aria-label="Ayuda: Excedente"')
        self.assertNotContains(operador, "Este cobro hizo pasar el tope mensual")
        self.assertNotContains(operador, "Excedente")
