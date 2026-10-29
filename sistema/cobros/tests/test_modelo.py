import re
from decimal import Decimal

from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Sum

from sistema.cobros import servicios
from sistema.cobros.models import Cobro, ItemCobro, ruta_comprobante
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.tests.test_efectivo import PACIENTE, item
from sistema.cobros.tests.test_transferencia import BaseTransferencia
from sistema.core.tests import SistemaTestCase
from sistema.dentalink.cliente import DentalinkNoDisponible
from sistema.dentalink.models import Banco
from sistema.destinatarios.models import Cuenta, Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.models import Terminal

D = Decimal


class ModeloBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        dest = Destinatario.objects.create(nombre="Test", tipo="clinica", cuit="20000000001")
        self.cuenta = Cuenta.objects.create(
            destinatario=dest,
            tipo="transferencia",
            alias="alias.uno",
            tope_mensual=D("100000.00"),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )
        prov = Proveedor.objects.get(nombre="Mercadopago")
        self.pos = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=dest,
            titular="Titular Test",
            proveedor=prov,
            objetivo=D("300000.00"),
        )
        self.terminal = Terminal.objects.create(nombre="T1", proveedor=prov, acepta_qr=True)
        self.otra = Terminal.objects.create(nombre="T2", proveedor=prov, acepta_qr=True)
        self.banco = Banco.objects.create(id_dentalink=1, nombre="Banco Uno")

    def crear(self, **kw):
        """Cobro válido de base con `kw` pisando; `objects.create` salta clean()."""
        base = dict(
            cuenta=self.cuenta,
            monto=D("30000.00"),
            metodo="transferencia",
            estado="finalizado",
            comprobante="comprobantes/a.pdf",
        )
        base.update(kw)
        return Cobro.objects.create(**base)

    def pos_kw(self, **kw):
        base = dict(
            cuenta=self.pos,
            metodo="qr",
            terminal=self.terminal,
            monto_neto=D("30000.00"),
            numero_operacion="88213",
        )
        base.update(kw)
        return base

    def falla(self, **kw):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.crear(**kw)


class ConstraintsCobroTest(ModeloBase):
    def test_efectivo_pendiente_falla(self):
        self.falla(metodo="efectivo", estado="pendiente", comprobante="")

    def test_debito_sin_terminal_falla(self):
        self.falla(
            **self.pos_kw(metodo="debito", terminal=None, banco_emisor=self.banco, ultimos_4="1234")
        )

    def test_efectivo_con_terminal_falla(self):
        self.falla(metodo="efectivo", terminal=self.terminal)

    def test_neto_un_centavo_arriba_del_bruto_falla(self):
        self.falla(**self.pos_kw(monto_neto=D("30000.01"), comprobante="x.pdf"))

    def test_neto_igual_al_bruto_pasa(self):
        self.assertIsNotNone(self.crear(**self.pos_kw()).pk)

    def test_neto_cero_falla(self):
        self.falla(**self.pos_kw(monto_neto=D("0.00")))

    def test_neto_un_centavo_pasa(self):
        self.assertIsNotNone(self.crear(**self.pos_kw(monto_neto=D("0.01"))).pk)

    def test_transferencia_con_numero_operacion_falla(self):
        self.falla(numero_operacion="88213")

    def test_qr_sin_numero_operacion_falla(self):
        self.falla(**self.pos_kw(numero_operacion=None))

    def test_credito_sin_banco_falla(self):
        self.falla(**self.pos_kw(metodo="credito", ultimos_4="1234"))

    def test_credito_con_ultimos_4_no_numericos_falla(self):
        self.falla(**self.pos_kw(metodo="credito", banco_emisor=self.banco, ultimos_4="43a1"))

    def test_credito_completo_pasa(self):
        kw = self.pos_kw(metodo="credito", banco_emisor=self.banco, ultimos_4="4321")
        self.assertIsNotNone(self.crear(**kw).pk)

    def test_qr_con_ultimos_4_falla(self):
        self.falla(**self.pos_kw(ultimos_4="1234"))

    def test_transferencia_finalizado_sin_comprobante_falla(self):
        self.falla(comprobante="")

    def test_transferencia_pendiente_sin_comprobante_pasa(self):
        self.assertIsNotNone(self.crear(estado="pendiente", comprobante="").pk)

    def test_debito_sin_comprobante_falla(self):
        self.falla(
            **self.pos_kw(
                metodo="debito", banco_emisor=self.banco, ultimos_4="1234", comprobante=""
            )
        )

    def test_cancelado_sin_motivo_falla(self):
        self.falla(estado="cancelado")

    def test_pendiente_con_motivo_falla(self):
        self.falla(estado="pendiente", comprobante="", motivo_cancelacion="no_pago")

    def test_motivo_otro_sin_detalle_falla(self):
        self.falla(estado="cancelado", motivo_cancelacion="otro")

    def test_motivo_otro_con_detalle_pasa(self):
        c = self.crear(estado="cancelado", motivo_cancelacion="otro", detalle_cancelacion="x")
        self.assertIsNotNone(c.pk)

    def test_operacion_repetida_en_la_terminal_falla(self):
        self.crear(**self.pos_kw())
        self.falla(**self.pos_kw())

    def test_operacion_repetida_en_otra_terminal_pasa(self):
        self.crear(**self.pos_kw())
        self.assertIsNotNone(self.crear(**self.pos_kw(terminal=self.otra)).pk)

    def test_operacion_repetida_con_el_primero_cancelado_pasa(self):
        self.crear(**self.pos_kw(estado="cancelado", motivo_cancelacion="no_pago"))
        self.assertIsNotNone(self.crear(**self.pos_kw()).pk)


class ConstraintsItemTest(ModeloBase):
    def setUp(self):
        super().setUp()
        self.cobro = cobro_prueba(cuenta=self.cuenta, monto=D("100.00"))

    def item(self, numero, **kw):
        return ItemCobro.objects.create(cobro=self.cobro, numero=numero, monto=D("1.00"), **kw)

    def test_misma_prestacion_en_el_cobro_falla(self):
        self.item(1, id_prestacion=5)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.item(2, id_prestacion=5)

    def test_prestaciones_distintas_pasan(self):
        self.item(1, id_prestacion=5)
        self.assertIsNotNone(self.item(2, id_prestacion=6).pk)

    def test_dos_items_historicos_sin_prestacion_pasan(self):
        self.item(1)
        self.assertIsNotNone(self.item(2).pk)

    def test_dos_saldo_a_favor_fallan(self):
        self.item(1, saldo_a_favor=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.item(2, saldo_a_favor=True)

    def test_un_saldo_a_favor_y_otro_normal_pasan(self):
        self.item(1, saldo_a_favor=True)
        self.assertIsNotNone(self.item(2).pk)


class NormalizacionTest(ModeloBase):
    def limpio(self, operacion):
        c = Cobro(**self.pos_kw(numero_operacion=operacion), monto=D("30000.00"))
        c.estado = "finalizado"
        c.comprobante = "x.pdf"
        c.full_clean(exclude=["cuenta"])
        return c

    def test_espacios_se_sacan(self):
        self.assertEqual(self.limpio(" 88 213 ").numero_operacion, "88213")

    def test_minusculas_pasan_a_mayusculas(self):
        self.assertEqual(self.limpio("ab12").numero_operacion, "AB12")

    def test_treinta_caracteres_pasan(self):
        self.assertEqual(len(self.limpio("A" * 30).numero_operacion), 30)

    def test_treinta_y_un_caracteres_fallan(self):
        with self.assertRaises(ValidationError):
            self.limpio("A" * 31)

    def test_ultimos_4_vacio_pasa_a_none(self):
        c = Cobro(cuenta=self.cuenta, monto=D("1.00"), ultimos_4="")
        c.clean()
        self.assertIsNone(c.ultimos_4)

    def test_ruta_comprobante(self):
        ruta = ruta_comprobante(None, "Foto.PDF")
        self.assertRegex(ruta, re.compile(r"^comprobantes/\d{4}/\d{2}/[0-9a-f]{32}\.pdf$"))


class SumaDeItemsTest(BaseTransferencia):
    def test_sc001_ningun_cobro_sin_marca_tiene_monto_distinto_de_la_suma(self):
        transferencia = self.alta()
        with mock.patch(PACIENTE, side_effect=DentalinkNoDisponible):
            completo = servicios.crear_cobro(
                usuario=self.ana, metodo="efectivo", monto=D("15000.00"), nombre_paciente="Ana"
            )
            sin_completar = servicios.crear_cobro(
                usuario=self.ana, metodo="efectivo", monto=D("5000.00"), nombre_paciente="Luis"
            )
        servicios.completar_items(
            completo, usuario=self.ana, id_paciente=501, items=[item(2, "15000.00")]
        )
        malos = (
            Cobro.objects.filter(pk__in=[transferencia.pk, completo.pk, sin_completar.pk])
            .filter(faltan_items=False)
            .annotate(suma=Sum("items__monto"))
            .exclude(suma=F("monto"))
        )
        self.assertEqual(list(malos), [])
        self.assertTrue(Cobro.objects.get(pk=sin_completar.pk).faltan_items)
