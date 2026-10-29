from datetime import datetime
from decimal import Decimal
from importlib import import_module

from django.apps import apps
from django.db import IntegrityError, transaction

from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import TopeVigente
from sistema.destinatarios.servicios import (
    crear_cuenta,
    crear_destinatario,
    editar_cuenta,
    tope_de,
)

D = Decimal
ALTA = datetime(2026, 9, 1, 8, 0, tzinfo=ARG)


class TopeVigenteTest(SistemaTestCase):
    def setUp(self):
        self.dest = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo", tipo="profesional"
        )
        self.c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="alias.uno",
            tope_mensual=D("500000.00"),
            tope_semanal=D("150000.00"),
            minimo_por_pago=D("10000.00"),
        )
        self.c.topes.update(desde=ALTA)

    def poner(self, tipo, monto, desde):
        return TopeVigente.objects.create(cuenta=self.c, tipo=tipo, monto=D(monto), desde=desde)

    def test_alta_graba_un_registro_mensual_y_uno_semanal_desde_fecha_alta(self):
        c = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="otra",
            tope_mensual=D("200000.00"),
            tope_semanal=D("0.00"),
        )
        self.assertEqual(
            {(t.tipo, t.monto, t.desde) for t in c.topes.all()},
            {("mensual", D("200000.00"), c.fecha_alta), ("semanal", D("0.00"), c.fecha_alta)},
        )

    def test_cuenta_que_no_es_transferencia_no_tiene_registros(self):
        c = crear_cuenta(usuario=None, tipo="efectivo", destinatario=self.dest)
        self.assertEqual(c.topes.count(), 0)

    def test_us3_4_semana_cerrada_conserva_el_tope_de_su_semana(self):
        # Semana 12/10 a 18/10: $150.000,00. El lunes 19/10 baja a $100.000,00.
        editar_cuenta(self.c, usuario=None, tope_semanal=D("100000.00"))
        self.c.topes.filter(tipo="semanal", monto=D("100000.00")).update(
            desde=datetime(2026, 10, 19, 9, 0, tzinfo=ARG)
        )
        self.assertEqual(
            tope_de(self.c, "semanal", datetime(2026, 10, 14, 12, 0, tzinfo=ARG)), D("150000.00")
        )
        self.assertEqual(
            tope_de(self.c, "semanal", datetime(2026, 10, 21, 12, 0, tzinfo=ARG)), D("100000.00")
        )

    def test_cambio_el_domingo_23_59_59_rige_para_esa_semana(self):
        self.poner("semanal", "100000.00", datetime(2026, 10, 18, 23, 59, 59, tzinfo=ARG))
        self.assertEqual(
            tope_de(self.c, "semanal", datetime(2026, 10, 13, 12, 0, tzinfo=ARG)), D("100000.00")
        )

    def test_cambio_el_lunes_00_00_00_no_rige_para_la_semana_anterior(self):
        self.poner("semanal", "100000.00", datetime(2026, 10, 19, 0, 0, 0, tzinfo=ARG))
        anterior = datetime(2026, 10, 18, 12, 0, tzinfo=ARG)
        self.assertEqual(tope_de(self.c, "semanal", anterior), D("150000.00"))
        # y esa semana nueva ya usa el nuevo
        self.assertEqual(
            tope_de(self.c, "semanal", datetime(2026, 10, 19, 12, 0, tzinfo=ARG)), D("100000.00")
        )

    def test_cambio_un_segundo_antes_del_cierre_del_mes_rige_para_ese_mes(self):
        self.poner("mensual", "400000.00", datetime(2026, 10, 31, 23, 59, 59, tzinfo=ARG))
        self.assertEqual(
            tope_de(self.c, "mensual", datetime(2026, 10, 5, tzinfo=ARG)), D("400000.00")
        )

    def test_cambio_justo_al_cierre_del_mes_no_rige_para_ese_mes(self):
        self.poner("mensual", "400000.00", datetime(2026, 11, 1, 0, 0, 0, tzinfo=ARG))
        self.assertEqual(
            tope_de(self.c, "mensual", datetime(2026, 10, 31, 12, 0, tzinfo=ARG)), D("500000.00")
        )
        self.assertEqual(
            tope_de(self.c, "mensual", datetime(2026, 11, 1, 0, 0, tzinfo=ARG)), D("400000.00")
        )

    def test_cambio_un_segundo_despues_del_cierre_no_rige_para_ese_mes(self):
        self.poner("mensual", "400000.00", datetime(2026, 11, 1, 0, 0, 1, tzinfo=ARG))
        self.assertEqual(
            tope_de(self.c, "mensual", datetime(2026, 10, 15, tzinfo=ARG)), D("500000.00")
        )

    def test_dos_cambios_en_el_mismo_mes_rige_el_ultimo_para_todo_el_mes(self):
        self.poner("mensual", "450000.00", datetime(2026, 10, 10, 9, 0, tzinfo=ARG))
        self.poner("mensual", "420000.00", datetime(2026, 10, 20, 9, 0, tzinfo=ARG))
        for dia in (1, 10, 15, 31):
            self.assertEqual(
                tope_de(self.c, "mensual", datetime(2026, 10, dia, 12, 0, tzinfo=ARG)),
                D("420000.00"),
            )

    def test_mes_cerrado_conserva_su_valor_tras_editar_el_tope_actual(self):
        editar_cuenta(self.c, usuario=None, tope_mensual=D("300000.00"))
        self.assertEqual(
            tope_de(self.c, "mensual", datetime(2026, 9, 15, tzinfo=ARG)), D("500000.00")
        )

    def test_cambio_en_el_periodo_corriente_rige_en_todo_el_periodo(self):
        editar_cuenta(self.c, usuario=None, tope_mensual=D("300000.00"))
        self.c.refresh_from_db()
        ahora = self.c.topes.filter(tipo="mensual").order_by("-desde").first().desde
        self.assertEqual(tope_de(self.c, "mensual", ahora), D("300000.00"))
        primero = ahora.astimezone(ARG).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(tope_de(self.c, "mensual", primero), D("300000.00"))

    def test_semanal_cero_sin_tope_queda_en_el_historial(self):
        editar_cuenta(self.c, usuario=None, tope_semanal=D("0.00"))
        ultimo = self.c.topes.filter(tipo="semanal").order_by("-desde").first()
        self.assertEqual(ultimo.monto, D("0.00"))
        self.assertEqual(tope_de(self.c, "semanal", ultimo.desde), D("0.00"))

    def test_periodo_anterior_al_primer_registro_devuelve_el_mas_antiguo(self):
        self.assertEqual(
            tope_de(self.c, "mensual", datetime(2026, 1, 15, tzinfo=ARG)), D("500000.00")
        )
        self.assertEqual(
            tope_de(self.c, "semanal", datetime(2026, 1, 15, tzinfo=ARG)), D("150000.00")
        )

    def test_editar_sin_cambiar_el_tope_no_graba_registro(self):
        editar_cuenta(self.c, usuario=None, tope_mensual=D("500000.00"), banco="Galicia")
        self.assertEqual(self.c.topes.count(), 2)

    def test_editar_un_tope_graba_solo_ese(self):
        editar_cuenta(self.c, usuario=None, tope_mensual=D("600000.00"))
        self.assertEqual(self.c.topes.filter(tipo="mensual").count(), 2)
        self.assertEqual(self.c.topes.filter(tipo="semanal").count(), 1)

    def test_constraints_monto_negativo_mensual_cero_y_duplicado(self):
        for tipo, monto in (("semanal", "-0.01"), ("mensual", "0.00")):
            with self.assertRaises(IntegrityError), transaction.atomic():
                self.poner(tipo, monto, datetime(2026, 10, 1, tzinfo=ARG))
        self.poner("semanal", "0.00", datetime(2026, 10, 1, tzinfo=ARG))  # semanal 0,00 vale
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.poner("semanal", "5.00", datetime(2026, 10, 1, tzinfo=ARG))

    def test_migracion_de_datos_crea_un_registro_por_tope_y_por_cuenta(self):
        efectivo = crear_cuenta(usuario=None, tipo="efectivo", destinatario=self.dest)
        TopeVigente.objects.all()._raw_delete("default")  # simula cuentas anteriores a 0007
        import_module("sistema.destinatarios.migrations.0008_topes_iniciales").crear_topes(
            apps, None
        )
        self.assertEqual(
            {(t.tipo, t.monto, t.desde) for t in self.c.topes.all()},
            {
                ("mensual", D("500000.00"), self.c.fecha_alta),
                ("semanal", D("150000.00"), self.c.fecha_alta),
            },
        )
        self.assertEqual(efectivo.topes.count(), 0)
        self.assertEqual(TopeVigente.objects.count(), 2)
