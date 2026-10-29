from datetime import datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.cobros.models import PagoComplementario
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
ARG = ZoneInfo("America/Argentina/Buenos_Aires")
AHORA = datetime(2026, 10, 28, 12, 0, tzinfo=ARG)
EN_MES = datetime(2026, 10, 10, 10, 0, tzinfo=ARG)


class ListaDestinatariosTest(SistemaTestCase):
    def setUp(self):
        p = mock.patch("django.utils.timezone.now", return_value=AHORA)
        p.start()
        self.addCleanup(p.stop)
        self.admin = get_user_model().objects.create_user(
            "adm017", email="adm017@example.com", role="administrator"
        )
        self.client.force_login(self.admin)
        self.url = reverse("destinatarios:lista")

    def dest(self, nombre, alias, cbu=None, tope="1000000.00", estado="activa", n=0):
        d = crear_destinatario(usuario=None, nombre=nombre, tipo="profesional")
        self.cuenta(d, alias, cbu, tope, estado)
        return d

    def cuenta(self, d, alias, cbu=None, tope="1000000.00", estado="activa"):
        return crear_cuenta(
            usuario=None, tipo="transferencia", destinatario=d, alias=alias, cbu=cbu or "",
            tope_mensual=D(tope), estado=estado,
        )  # fmt: skip

    def fila(self, r, nombre):
        return [d for d in r.context["destinatarios"] if d.nombre == nombre][0]

    def test_q_por_cbu_exacto_encuentra_uno(self):
        a = self.dest("Dueño Uno", "ejemplo.uno", "0000000000000000000001")
        self.dest("Dueño Dos", "ejemplo.dos", "0000000000000000000002")
        r = self.client.get(self.url, {"q": "0000000000000000000001"})
        self.assertEqual([d.pk for d in r.context["destinatarios"]], [a.pk])

    def test_q_por_alias_sin_distinguir_mayusculas(self):
        a = self.dest("Dueño Uno", "ejemplo.uno")
        self.dest("Dueño Dos", "ejemplo.dos")
        r = self.client.get(self.url, {"q": "EJEMPLO.UNO"})
        self.assertEqual([d.pk for d in r.context["destinatarios"]], [a.pk])

    def test_q_con_dos_cuentas_que_coinciden_da_una_fila(self):
        a = self.dest("Dueño Uno", "ejemplo.uno")
        self.cuenta(a, "ejemplo.uno.bis")
        r = self.client.get(self.url, {"q": "ejemplo.uno"})
        self.assertEqual(len(r.context["destinatarios"]), 1)

    def test_q_combinada_con_estado(self):
        self.dest("Dueño Uno", "ejemplo.uno")
        self.dest("Dueño Dos", "ejemplo.dos")
        r = self.client.get(self.url, {"q": "ejemplo", "estado": "inactivo"})
        self.assertEqual(len(r.context["destinatarios"]), 0)
        r = self.client.get(self.url, {"q": "ejemplo", "estado": "activo"})
        self.assertEqual(len(r.context["destinatarios"]), 2)

    def test_ejemplo_fr025_suma_cuentas_activas_e_ignora_inactiva(self):
        d = crear_destinatario(usuario=None, nombre="Dueño Tres", tipo="profesional")
        a = self.cuenta(d, "cuenta.a", tope="1000000.00")
        b = self.cuenta(d, "cuenta.b", tope="500000.00")
        c = self.cuenta(d, "cuenta.c", tope="900000.00", estado="inactiva")
        cobro_prueba(cuenta=a, monto=D("600000.00"), estado="finalizado", fecha=EN_MES)
        cobro_prueba(cuenta=a, monto=D("100000.00"), estado="pendiente", fecha=EN_MES)
        cobro_prueba(cuenta=b, monto=D("550000.00"), estado="finalizado", fecha=EN_MES)
        cobro_prueba(cuenta=c, monto=D("100000.00"), estado="finalizado", fecha=EN_MES)
        r = self.client.get(self.url)
        for texto in ("$1.500.000,00", "$1.250.000,00", "$300.000,00"):
            self.assertContains(r, texto)
        self.assertEqual(self.fila(r, "Dueño Tres").cupo["restante"], D("300000.00"))

    def test_casos_limite_del_restante(self):
        for consumido, restante in [
            ("999999.99", "0.01"),
            ("1000000.00", "0.00"),
            ("1000000.01", "0.00"),
            ("0.00", "1000000.00"),
        ]:
            with self.subTest(consumido=consumido):
                d = self.dest(f"Dueño {consumido}", f"a.{consumido}")
                if D(consumido):
                    cobro_prueba(
                        cuenta=d.cuentas.get(), monto=D(consumido), estado="finalizado",
                        fecha=EN_MES,
                    )  # fmt: skip
                r = self.client.get(self.url)
                self.assertEqual(self.fila(r, d.nombre).cupo["restante"], D(restante))
                self.assertEqual(self.fila(r, d.nombre).cupo["consumido"], D(consumido))

    def test_sin_cuenta_de_transferencia_activa_muestra_rayas(self):
        self.dest("Dueño Inactivo", "inactiva.uno", estado="inactiva")
        r = self.client.get(self.url)
        self.assertIsNone(self.fila(r, "Dueño Inactivo").cupo)
        self.assertContains(r, "—", count=None)

    def test_pago_complementario_registrado_suma_al_consumido(self):
        d = self.dest("Dueño Pago", "pago.uno")
        PagoComplementario.objects.create(
            destinatario=d, mes=EN_MES.date().replace(day=1), monto=D("250000.00"),
            metodo="transferencia", cuenta=d.cuentas.get(), creado_por=self.admin,
        )  # fmt: skip
        r = self.client.get(self.url)
        self.assertEqual(self.fila(r, "Dueño Pago").cupo["consumido"], D("250000.00"))

    def test_operador_y_profesional_reciben_403_y_sin_sesion_va_al_login(self):
        for rol in ("operator", "professional"):
            u = get_user_model().objects.create_user(rol, email=f"{rol}@example.com", role=rol)
            self.client.force_login(u)
            self.assertEqual(self.client.get(self.url, {"q": "x"}).status_code, 403, rol)
        self.client.logout()
        self.assertIn("/login/", self.client.get(self.url)["Location"])
