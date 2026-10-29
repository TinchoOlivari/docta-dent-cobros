from datetime import date, datetime, time
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.auditoria.servicios import registrar
from sistema.automatizacion.servicios import crear_regla
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal


class FichaTest(SistemaTestCase):
    def setUp(self):
        p = mock.patch(
            "django.utils.timezone.now", return_value=datetime(2026, 10, 28, 12, 0, tzinfo=ARG)
        )
        p.start()
        self.addCleanup(p.stop)
        admin = get_user_model().objects.create_user(
            "adm017f", email="adm017f@example.com", role="administrator"
        )
        self.client.force_login(admin)
        self.d = crear_destinatario(usuario=None, nombre="Dueño Ficha", tipo="profesional")
        self.c = crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=self.d, alias="ejemplo.uno",
            tope_mensual=D("1000000.00"), tope_semanal=D("200000.00"),
        )  # fmt: skip
        self.url = reverse("destinatarios:detalle", args=[self.d.pk])

    def test_mes_valido_da_5_filas_por_cuenta_de_transferencia(self):
        r = self.client.get(self.url, {"mes": "2026-10"})
        ((cuenta, filas),) = r.context["semanas"]
        self.assertEqual((cuenta.pk, len(filas)), (self.c.pk, 5))
        self.assertContains(r, "28/09–04/10")

    def test_mes_invalido_usa_el_mes_corriente(self):
        for mes in ("xyz", "2026-13", "2026-1x", ""):
            r = self.client.get(self.url, {"mes": mes})
            self.assertEqual(r.context["mes"], date(2026, 10, 1), mes)

    def test_reglas_activa_con_ejecucion_e_inactiva_nunca(self):
        a = crear_regla(
            usuario=None, nombre="Regla A", cuenta=self.c, disparador="semana", dia=1,
            hora=time(8, 0), accion="primero",
        )  # fmt: skip
        crear_regla(
            usuario=None, nombre="Regla B", cuenta=self.c, disparador="semana", dia=2,
            hora=time(9, 0), accion="ultimo", estado="inactiva",
        )  # fmt: skip
        with mock.patch(
            "django.utils.timezone.now", return_value=datetime(2026, 10, 19, 8, 0, tzinfo=ARG)
        ):
            registrar("ejecucion_regla", usuario=None, entidad=a, exito=True)
        r = self.client.get(self.url)
        self.assertEqual(len(r.context["reglas"]), 2)
        self.assertContains(r, "19/10/2026 08:00 · Sin cambios")
        self.assertContains(r, "Nunca")

    def test_mover_por_post_sigue_andando(self):
        otra = crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=self.d, alias="ejemplo.dos",
            tope_mensual=D("1000.00"),
        )  # fmt: skip
        self.client.post(reverse("asignacion:mover", args=[otra.pk]), {"posicion": 1})
        otra.refresh_from_db()
        self.assertEqual(otra.posicion, 1)

    def test_operador_y_profesional_reciben_403(self):
        for rol in ("operator", "professional"):
            u = get_user_model().objects.create_user(rol, email=f"{rol}@example.com", role=rol)
            self.client.force_login(u)
            self.assertEqual(self.client.get(self.url, {"mes": "2026-10"}).status_code, 403)
        self.client.logout()
        self.assertIn("/login/", self.client.get(self.url)["Location"])
