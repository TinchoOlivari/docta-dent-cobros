from datetime import datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.asignacion.servicios import (
    SinCuentaDisponible,
    crear_cobro_pendiente,
    lista_con_cupo,
)
from sistema.auditoria.models import Evento
from sistema.cobros.models import Cobro
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
ARG = ZoneInfo("America/Argentina/Buenos_Aires")
MENSAJE = "No hay cuenta disponible. Cobrá con otro método de cobro."
# Miércoles; el lunes de su semana es el 26/10 y el mes es octubre.
AHORA = datetime(2026, 10, 28, 12, 0, tzinfo=ARG)
INICIO_MES = datetime(2026, 10, 2, 10, 0, tzinfo=ARG)  # viernes de la semana anterior


class DatosEjemplo(SistemaTestCase):
    """Los 3 datos de ejemplo de la spec, posiciones 1, 2, 3."""

    def setUp(self):
        patcher = mock.patch("django.utils.timezone.now", return_value=AHORA)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = get_user_model().objects.create_user(
            "asig-admin", email="asig@example.com", role="administrator"
        )
        self.dm = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo", tipo="profesional"
        )
        self.dj = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        self.dr = crear_destinatario(usuario=None, nombre="Rodrigo", tipo="profesional")
        self.c1 = self.transf(self.dm, "alias.uno", "500000.00", "0.00", "10000.00")
        self.c2 = self.transf(self.dj, "jimena.roble.rana", "300000.00", "100000.00", "0.00")
        self.c3 = self.transf(self.dr, "rodrigo.mp", "200000.00", "0.00", "50000.00")

    def transf(self, dest, alias, mensual, semanal, minimo):
        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias=alias,
            tope_mensual=D(mensual),
            tope_semanal=D(semanal),
            minimo_por_pago=D(minimo),
        )

    def cobro(self, cuenta, monto, estado="finalizado", fecha=None):
        return cobro_prueba(cuenta=cuenta, monto=D(monto), estado=estado, fecha=fecha or INICIO_MES)

    def pedir(self, monto):
        return crear_cobro_pendiente(monto=D(monto), usuario=self.user).cuenta.alias


class AsignarTest(DatosEjemplo):
    def test_us1_1_va_a_la_primera_con_cupo(self):
        self.cobro(self.c1, "450000.00")
        self.assertEqual(self.pedir("50000.00"), "alias.uno")

    def test_us1_2_un_centavo_arriba_del_cupo_pasa_a_la_siguiente(self):
        self.cobro(self.c1, "450000.00")
        self.assertEqual(self.pedir("50000.01"), "jimena.roble.rana")

    def test_us1_3_un_centavo_abajo_del_minimo_pasa_a_la_siguiente(self):
        self.assertEqual(self.pedir("9999.99"), "jimena.roble.rana")

    def test_us1_4_justo_en_el_minimo_entra(self):
        self.assertEqual(self.pedir("10000.00"), "alias.uno")

    def test_us1_5_justo_en_el_cupo_semanal_entra(self):
        self.cobro(self.c1, "500000.00")
        self.cobro(self.c2, "60000.00", fecha=AHORA)
        self.assertEqual(self.pedir("40000.00"), "jimena.roble.rana")

    def test_us1_5_un_centavo_arriba_del_cupo_semanal_no_entra(self):
        self.cobro(self.c1, "500000.00")
        self.cobro(self.c2, "60000.00", fecha=AHORA)
        with self.assertRaises(SinCuentaDisponible):
            self.pedir("40000.01")

    def test_us1_6_bajo_el_minimo_de_la_ultima_no_hay_cuenta(self):
        self.cobro(self.c1, "500000.00")
        self.cobro(self.c2, "300000.00")
        with self.assertRaises(SinCuentaDisponible):
            self.pedir("49999.99")

    def test_us1_7_no_parte_el_monto(self):
        self.cobro(self.c1, "300000.00")
        antes = Cobro.objects.count()
        with self.assertRaises(SinCuentaDisponible) as ctx:
            self.pedir("250000.00")
        self.assertEqual(ctx.exception.messages, [MENSAJE])
        self.assertEqual(Cobro.objects.count(), antes)

    def test_lista_vacia_no_hay_cuenta(self):
        Cuenta.objects.update(posicion=None, estado="inactiva")
        with self.assertRaises(SinCuentaDisponible):
            self.pedir("100.00")

    def test_el_cobro_nace_pendiente_con_fecha_usuario_y_alta(self):
        cobro = crear_cobro_pendiente(monto=D("20000.00"), usuario=self.user)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "pendiente")
        self.assertEqual(cobro.fecha, AHORA)
        self.assertEqual(cobro.creado_por, self.user)
        self.assertFalse(cobro.excedente)
        eventos = Evento.objects.filter(entidad="cobros.cobro", entidad_id=cobro.pk)
        self.assertEqual(eventos.count(), 1)

    def test_datos_extra_se_guardan_y_destinatario_es_el_de_la_cuenta(self):
        cobro = crear_cobro_pendiente(
            monto=D("20000.00"),
            usuario=self.user,
            metodo="transferencia",
            nombre_paciente="Juan Paciente",
            observaciones="x",
        )
        cobro.refresh_from_db()
        self.assertEqual(cobro.nombre_paciente, "Juan Paciente")
        self.assertEqual(cobro.observaciones, "x")
        self.assertEqual(cobro.destinatario, cobro.cuenta.destinatario)
        self.assertIsInstance(cobro.monto, D)

    def test_minimo_cero_acepta_un_centavo(self):
        self.cobro(self.c1, "500000.00")
        self.assertEqual(self.pedir("0.01"), "jimena.roble.rana")

    def test_monto_cero_no_se_asigna(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.pedir("0.00")

    def test_fr007_cuenta_del_mismo_destinatario_no_comparte_cupo(self):
        self.transf(self.dm, "martin.piedra.casa", "100000.00", "0.00", "0.00")
        self.cobro(self.c1, "500000.00")
        self.assertEqual(self.pedir("50000.00"), "jimena.roble.rana")
        self.cobro(self.c2, "300000.00")
        self.cobro(self.c3, "200000.00")
        self.assertEqual(self.pedir("50000.00"), "martin.piedra.casa")

    def test_sc004_cantidad_de_consultas_no_depende_de_las_cuentas(self):
        def consultas():
            with CaptureQueriesContext(connection) as ctx:
                with self.assertRaises(SinCuentaDisponible):
                    self.pedir("1.00")  # bajo todos los mínimos: recorre toda la lista
            return len(ctx)

        for cuenta in (self.c2, self.c3):
            Cuenta.objects.filter(pk=cuenta.pk).update(minimo_por_pago=D("10000.00"))
        con_3 = consultas()
        for i in range(27):
            self.transf(self.dj, f"alias.extra.{i}", "100000.00", "0.00", "10000.00")
        self.assertEqual(consultas(), con_3)


class ListaConCupoTest(DatosEjemplo):
    def cupos(self, momento=AHORA):
        lista = {c.alias: c for c in lista_con_cupo(momento)}
        return lista["alias.uno"], lista["jimena.roble.rana"]

    def test_sin_cobros_el_cupo_es_el_tope(self):
        t, j = self.cupos()
        self.assertEqual(t.cupo_mensual, D("500000.00"))
        self.assertEqual(j.cupo_semanal, D("100000.00"))

    def test_cupo_mensual_en_el_limite(self):
        self.cobro(self.c1, "1.00")
        for recibido, cupo in (
            ("499999.99", "0.01"),
            ("500000.00", "0.00"),
            ("500000.01", "0.00"),  # nunca negativo
        ):
            with self.subTest(recibido=recibido):
                Cobro.objects.all().update(monto=D(recibido))
                self.assertEqual(self.cupos()[0].cupo_mensual, D(cupo))

    def test_cupo_semanal_en_el_limite(self):
        self.cobro(self.c2, "1.00", fecha=AHORA)
        for recibido, cupo in (
            ("99999.99", "0.01"),
            ("100000.00", "0.00"),
            ("100000.01", "0.00"),
        ):
            with self.subTest(recibido=recibido):
                Cobro.objects.all().update(monto=D(recibido))
                self.assertEqual(self.cupos()[1].cupo_semanal, D(cupo))

    def test_pendiente_y_finalizado_suman_y_cancelado_no(self):
        self.cobro(self.c1, "300000.00")
        self.cobro(self.c1, "150000.00", estado="pendiente")
        self.cobro(self.c1, "40000.00", estado="cancelado")
        t = self.cupos()[0]
        self.assertEqual((t.recibido_mes, t.reservado_mes), (D("300000.00"), D("150000.00")))
        self.assertEqual(t.cupo_mensual, D("50000.00"))

    def test_tope_semanal_cero_es_sin_limite(self):
        self.assertIsNone(self.cupos()[0].cupo_semanal)

    def test_domingo_ultimo_segundo_cuenta_en_la_semana_del_lunes_anterior(self):
        domingo = datetime(2026, 11, 1, 23, 59, 59, tzinfo=ARG)
        self.cobro(self.c2, "1000.00", fecha=domingo)
        _, j = self.cupos(domingo)
        self.assertEqual(j.cupo_semanal, D("99000.00"))
        self.assertEqual(j.cupo_mensual, D("299000.00"))  # noviembre

    def test_lunes_cero_horas_no_cuenta_en_la_semana_anterior(self):
        lunes = datetime(2026, 11, 2, 0, 0, 0, tzinfo=ARG)
        self.cobro(self.c2, "1000.00", fecha=lunes)
        _, j = self.cupos(datetime(2026, 11, 1, 23, 59, 59, tzinfo=ARG))
        self.assertEqual(j.cupo_semanal, D("100000.00"))
        _, j = self.cupos(lunes)
        self.assertEqual(j.cupo_semanal, D("99000.00"))

    def test_corte_de_mes_en_hora_argentina_no_utc(self):
        # 31/10 23:30 ARG = 1/11 02:30 UTC: sigue siendo octubre
        tarde = datetime(2026, 10, 31, 23, 30, tzinfo=ARG)
        self.cobro(self.c1, "1000.00", fecha=tarde)
        self.assertEqual(self.cupos(tarde)[0].cupo_mensual, D("499000.00"))
        noviembre = datetime(2026, 11, 1, 0, 0, tzinfo=ARG)
        self.assertEqual(self.cupos(noviembre)[0].cupo_mensual, D("500000.00"))

    def test_pendiente_de_septiembre_no_reserva_en_octubre(self):
        self.cobro(
            self.c1,
            "100000.00",
            estado="pendiente",
            fecha=datetime(2026, 9, 30, 23, 59, tzinfo=ARG),
        )
        self.assertEqual(self.cupos()[0].cupo_mensual, D("500000.00"))
