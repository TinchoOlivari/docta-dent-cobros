"""021 Fase 0: unaccent, paginación, esperado antes del primer tope, chequeo de SISTEMA_URL,
usuarios visibles, catálogo `_ui`, campos de fecha, CSV en streaming y títulos."""

from datetime import datetime
from decimal import Decimal as D
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from sistema.core import checks
from sistema.core.forms import FechaField, FechaHoraField, MesField
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import TopeVigente
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario, esperado_mensual
from sistema.reportes.descarga import respuesta
from sistema.usuarios.models import Perfil
from sistema.usuarios.servicios import usuarios_visibles

User = get_user_model()
CLAVE = "clave-de-siempre-1"


class EsperadoMensualTest(SistemaTestCase):
    def cuenta(self, desde):
        dest = crear_destinatario(usuario=None, nombre="Destinatario Ejemplo", tipo="clinica")
        c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="ejemplo.uno",
            tope_mensual=D("1000.00"),
        )
        TopeVigente.objects.filter(cuenta=c).update(desde=desde)
        return c

    def esperado(self, c, anio, mes):
        return esperado_mensual(c, datetime(anio, mes, 15, 12, tzinfo=ARG))

    def test_primer_registro_a_mitad_de_mes(self):
        c = self.cuenta(datetime(2026, 10, 15, tzinfo=ARG))
        self.assertEqual(self.esperado(c, 2026, 9), D("0.00"))
        self.assertEqual(self.esperado(c, 2026, 10), D("1000.00"))

    def test_primer_registro_justo_al_inicio_del_mes_siguiente(self):
        c = self.cuenta(datetime(2026, 10, 1, 0, 0, tzinfo=ARG))
        self.assertEqual(self.esperado(c, 2026, 9), D("0.00"))

    def test_primer_registro_un_segundo_antes_del_inicio(self):
        c = self.cuenta(datetime(2026, 9, 30, 23, 59, 59, tzinfo=ARG))
        self.assertEqual(self.esperado(c, 2026, 9), D("1000.00"))

    def test_sin_registros_es_el_valor_actual(self):
        c = self.cuenta(datetime(2026, 10, 1, tzinfo=ARG))
        TopeVigente.objects.filter(cuenta=c)._raw_delete("default")  # migrada sin historial
        c = type(c).objects.get(pk=c.pk)
        self.assertEqual(self.esperado(c, 2026, 9), D("1000.00"))


class ChequeoSistemaUrlTest(SistemaTestCase):
    def avisos(self, url, debug=False):
        with override_settings(SISTEMA_URL=url, DEBUG=debug):
            return checks.sistema_url_publica(None)

    def test_localhost_vacia_y_127_avisan(self):
        for url in ("http://localhost:8000", "", "http://127.0.0.1:8000"):
            with self.subTest(url=url):
                self.assertEqual([a.id for a in self.avisos(url)], ["sistema.W001"])

    def test_url_publica_no_avisa(self):
        self.assertEqual(self.avisos("https://cobros.example.com"), [])

    def test_con_debug_no_avisa(self):
        self.assertEqual(self.avisos("http://localhost:8000", debug=True), [])

    def test_check_deploy_termina_bien_solo_con_warning(self):
        salida = StringIO()
        with override_settings(SISTEMA_URL="http://localhost:8000", DEBUG=False):
            call_command("check", deploy=True, stdout=salida, stderr=salida)  # no lanza
        self.assertIn("sistema.W001", salida.getvalue())


class UsuariosVisiblesTest(SistemaTestCase):
    def test_sin_el_inactivo_sin_perfil(self):
        User.objects.create_user(
            "completar", email="c@example.com", role="operator", is_active=False
        )
        self.assertNotIn("completar", [u.username for u in usuarios_visibles()])

    def test_con_el_inactivo_con_perfil_y_el_activo_sin_perfil(self):
        viejo = User.objects.create_user(
            "viejo", email="v@example.com", role="operator", is_active=False
        )
        Perfil.objects.create(usuario=viejo)
        User.objects.create_user("nuevo", email="n@example.com", role="operator")
        nombres = [u.username for u in usuarios_visibles()]
        self.assertIn("viejo", nombres)
        self.assertIn("nuevo", nombres)


class CatalogoUiTest(SistemaTestCase):
    def entrar(self, rol):
        u = User.objects.create_user(
            f"u-{rol}", email=f"{rol}@example.com", role=rol, password=CLAVE
        )
        self.client.force_login(u)

    @override_settings(DEBUG=True)
    def test_operador_con_debug_es_403(self):
        self.entrar("operator")
        self.assertEqual(self.client.get(reverse("core:catalogo")).status_code, 403)

    @override_settings(DEBUG=False)
    def test_operador_sin_debug_es_404(self):
        self.entrar("operator")
        self.assertEqual(self.client.get(reverse("core:catalogo")).status_code, 404)

    @override_settings(DEBUG=False)
    def test_administrador_sin_debug_es_404(self):
        self.entrar("administrator")
        self.assertEqual(self.client.get(reverse("core:catalogo")).status_code, 404)


class CamposFechaTest(SistemaTestCase):
    def test_fecha(self):
        f = FechaField()
        self.assertEqual(f.clean("25/10/2026").isoformat(), "2026-10-25")
        self.assertEqual(f.clean("2026-10-25").isoformat(), "2026-10-25")
        for malo in ("31/02/2026", "25-10-2026", "ayer"):
            with self.subTest(malo=malo):
                with self.assertRaisesMessage(Exception, "Escribí la fecha como dd/mm/aaaa."):
                    f.clean(malo)

    def test_fecha_hora(self):
        f = FechaHoraField()
        self.assertEqual(f.clean(["25/10/2026", "09:05"]), datetime(2026, 10, 25, 9, 5, tzinfo=ARG))
        with self.assertRaisesMessage(Exception, "Escribí la hora como HH:MM."):
            f.clean(["25/10/2026", "9.05"])
        with self.assertRaisesMessage(Exception, "Escribí la fecha como dd/mm/aaaa."):
            f.clean(["31/02/2026", "09:05"])

    def test_mes(self):
        f = MesField()
        self.assertEqual(f.clean("2026-10").isoformat(), "2026-10-01")
        with self.assertRaises(Exception):
            f.clean("2026-13")


class CsvStreamingTest(SistemaTestCase):
    def test_tres_filas_igual_byte_a_byte(self):
        r = respuesta(
            "x", ["A", "B"], [["uno", D("1.50")], ["=dos", None], ["tres;cuatro", D("0")]], "csv"
        )
        self.assertTrue(r.streaming)
        esperado = '﻿A;B\r\nuno;1,50\r\n\'=dos;\r\n"tres;cuatro";0,00\r\n'.encode()
        self.assertEqual(b"".join(r.streaming_content), esperado)

    def test_nombre_propio_y_sin_nombre(self):
        con = respuesta("x", ["A"], [], "csv", nombre="falta-del-mes-2026-10")
        self.assertIn('filename="falta-del-mes-2026-10.csv"', con["Content-Disposition"])
        sin = respuesta("x", ["A"], [], "xlsx")
        self.assertRegex(sin["Content-Disposition"], r'filename="x-\d{2}-\d{2}-\d{4}\.xlsx"')
