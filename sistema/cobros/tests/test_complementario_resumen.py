from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model

from sistema.cobros import servicios
from sistema.cobros.tests.fabrica import cobro_prueba, pago_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Destinatario, Proveedor
from sistema.destinatarios.servicios import (
    cambiar_estado_cuenta,
    cambiar_estado_destinatario,
    crear_cuenta,
    crear_destinatario,
)

D = Decimal
OCT = date(2026, 10, 1)
NOV = date(2026, 11, 1)
EN_OCT = datetime(2026, 10, 15, 12, 0, tzinfo=ARG)


class ResumenMesTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        self.mp = Proveedor.objects.get(nombre="Mercadopago")
        self.n = 0

    def dest(self, nombre, tipo="profesional"):
        return crear_destinatario(usuario=None, nombre=nombre, tipo=tipo)

    def transf(self, dest, alias, tope):
        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias=alias,
            tope_mensual=D(tope),
        )

    def pos(self, dest, objetivo):
        return crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=dest,
            titular="Titular Prueba",
            proveedor=self.mp,
            objetivo=D(objetivo),
        )

    def cobro(self, dest, monto, fecha=EN_OCT, estado="finalizado", **kw):
        cuenta = dest.cuentas.first() or crear_cuenta(
            usuario=None, tipo="efectivo", destinatario=dest
        )
        return cobro_prueba(
            destinatario=dest, cuenta=cuenta, monto=D(monto), fecha=fecha, estado=estado, **kw
        )

    def pago(self, dest, monto, mes=OCT, **kw):
        kw.setdefault("creado_por", self.admin)
        if kw.get("metodo", "transferencia") == "transferencia":
            kw.setdefault("cuenta", dest.cuentas.filter(tipo="transferencia").first())
        return pago_prueba(destinatario=dest, monto=D(monto), mes=mes, **kw)

    def fila(self, dest, mes=OCT, **kw):
        return next((f for f in servicios.resumen_mes(mes, **kw) if f.pk == dest.pk), None)

    def test_us1_1_jimena_falta_30000(self):
        j = self.dest("Jimena")
        self.transf(j, "jimena.roble.rana", "600000.00")
        self.cobro(j, "570000.00")
        f = self.fila(j)
        self.assertEqual(
            (f.esperado, f.recibido, f.falta), (D("600000.00"), D("570000.00"), D("30000.00"))
        )
        self.assertEqual([c.alias for c in f.cuentas_transferencia], ["jimena.roble.rana"])

    def test_us1_2_esperado_suma_transferencias_y_pos(self):
        m = self.dest("Destinatario Ejemplo")
        self.transf(m, "alias.uno", "100000.00")
        self.transf(m, "alias.dos", "500000.00")
        self.pos(m, "550000.00")
        self.assertEqual(self.fila(m).esperado, D("1150000.00"))

    def test_us1_3_recibido_mayor_da_falta_negativa(self):
        r = self.dest("Rodrigo")
        self.transf(r, "rodrigo.alias", "500000.00")
        self.cobro(r, "510000.00")
        self.assertEqual(self.fila(r).falta, D("-10000.00"))
        self.assertIsNone(self.fila(r, con_falta=True))

    def test_us1_4_pendiente_y_cancelado_no_suman(self):
        j = self.dest("Jimena")
        self.transf(j, "jimena.roble.rana", "600000.00")
        self.cobro(j, "50000.00", estado="pendiente")
        self.cobro(j, "40000.00", estado="cancelado")
        self.assertEqual(self.fila(j).recibido, D("0.00"))

    def test_cobro_en_el_limite_del_mes(self):
        j = self.dest("Jimena")
        self.transf(j, "jimena.roble.rana", "600000.00")
        self.cobro(j, "1000.00", fecha=datetime(2026, 10, 31, 23, 59, 59, tzinfo=ARG))
        self.cobro(j, "2000.00", fecha=datetime(2026, 11, 1, 0, 0, 0, tzinfo=ARG))
        self.assertEqual(self.fila(j, OCT).recibido, D("1000.00"))
        self.assertEqual(self.fila(j, NOV).recibido, D("2000.00"))

    def test_pago_registrado_suma_y_anulado_no(self):
        j = self.dest("Jimena")
        self.transf(j, "jimena.roble.rana", "600000.00")
        self.pago(j, "10000.00")
        self.pago(j, "7000.00", estado="anulado")
        self.pago(j, "3000.00", mes=NOV)
        self.assertEqual(self.fila(j).recibido, D("10000.00"))

    def test_pago_en_efectivo_suma_al_recibido(self):
        j = self.dest("Jimena")
        self.transf(j, "jimena.roble.rana", "600000.00")
        self.pago(j, "5000.00", metodo="efectivo")
        self.assertEqual(self.fila(j).recibido, D("5000.00"))

    def test_falta_un_centavo_cero_y_negativo_con_filtro(self):
        a, b, c = self.dest("A"), self.dest("B"), self.dest("C")
        for d in (a, b, c):
            self.transf(d, f"alias.{d.nombre}", "1000.00")
        self.cobro(a, "999.99")
        self.cobro(b, "1000.00")
        self.cobro(c, "1000.01")
        con = servicios.resumen_mes(OCT, con_falta=True)
        self.assertEqual([f.pk for f in con], [a.pk])
        self.assertEqual(con[0].falta, D("0.01"))

    def test_cuentas_inactivas_y_efectivo_no_suman_al_esperado(self):
        j = self.dest("Jimena")
        self.transf(j, "activa", "100000.00")
        inactiva = self.transf(j, "inactiva", "200000.00")
        pos_inactiva = self.pos(j, "300000.00")
        crear_cuenta(usuario=None, tipo="efectivo", destinatario=j)
        cambiar_estado_cuenta(inactiva, "inactiva", usuario=None)
        cambiar_estado_cuenta(pos_inactiva, "inactiva", usuario=None)
        f = self.fila(j)
        self.assertEqual(f.esperado, D("100000.00"))
        self.assertEqual([c.alias for c in f.cuentas_transferencia], ["activa"])

    def test_destinatario_inactivo_aparece_y_archivado_no(self):
        i, a = self.dest("Inactivo"), self.dest("Archivado")
        for d in (i, a):
            self.transf(d, f"alias.{d.nombre}", "40000.00")
        cambiar_estado_destinatario(i, "inactivo", usuario=None)
        cambiar_estado_destinatario(a, "archivado", usuario=None)
        self.assertEqual(self.fila(i).falta, D("40000.00")) if self.fila(i) else self.fail()
        self.assertIsNone(self.fila(a))

    def test_clinica_solo_efectivo_no_aparece_con_falta(self):
        c = self.dest("Clínica", tipo="clinica")
        crear_cuenta(usuario=None, tipo="efectivo", destinatario=c)
        self.assertIsNone(self.fila(c))  # 021/FR-026: sin transferencia ni POS Activa no sale
        self.assertIsNone(self.fila(c, con_falta=True))

    def test_cobro_pos_suma_bruto_y_no_neto(self):
        m = self.dest("Destinatario Ejemplo")
        cuenta = self.pos(m, "550000.00")
        self.assertTrue(cuenta.pk)
        self.cobro(m, "100000.00", metodo="qr", monto_neto=D("95000.00"))
        self.assertEqual(self.fila(m).recibido, D("100000.00"))

    def test_cobro_de_otro_destinatario_no_suma(self):
        a, b = self.dest("A"), self.dest("B")
        self.transf(a, "alias.a", "1000.00")
        self.cobro(b, "500.00")
        self.assertEqual(self.fila(a).recibido, D("0.00"))

    def test_destinatarios_explicitos_incluyen_al_archivado(self):
        a = self.dest("Archivado")
        self.transf(a, "alias.archivado", "100000.00")
        self.cobro(a, "100000.00")
        cambiar_estado_destinatario(a, "archivado", usuario=None)
        self.assertIsNone(self.fila(a))
        filas = servicios.resumen_mes(OCT, destinatarios=Destinatario.objects.filter(pk=a.pk))
        self.assertEqual([f.pk for f in filas], [a.pk])
        self.assertEqual(filas[0].recibido, D("100000.00"))
