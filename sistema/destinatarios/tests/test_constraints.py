from decimal import Decimal

from django.db import DataError, IntegrityError, transaction
from django.utils import timezone

from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario, Proveedor

CBU = "0000000000000000000001"
D = Decimal


class ConstraintsTest(SistemaTestCase):
    def setUp(self):
        self.d = Destinatario.objects.create(nombre="Test", tipo="clinica", cuit="20000000001")
        self.prov = Proveedor.objects.first()

    def transf(self, **kw):
        base = dict(
            destinatario=self.d,
            tipo="transferencia",
            alias="alias.uno",
            tope_mensual=D("1000.00"),
            tope_semanal=D("500.00"),
            minimo_por_pago=D("100.00"),
        )
        base.update(kw)
        return base

    def pos(self, **kw):
        base = dict(
            destinatario=self.d,
            tipo="terminal_pos",
            proveedor=self.prov,
            objetivo=D("1000.00"),
            acumula_desde=timezone.now(),
        )
        base.update(kw)
        return base

    def efectivo(self, **kw):
        return dict(destinatario=self.d, tipo="efectivo", **kw)

    def falla(self, datos, nombre):
        with self.assertRaises((IntegrityError, DataError)) as cm, transaction.atomic():
            Cuenta.objects.create(**datos)
        # Un valor más largo que la columna lo frena el tipo varchar, antes que la constraint.
        if isinstance(cm.exception, IntegrityError):
            self.assertIn(nombre, str(cm.exception))

    def anda(self, datos):
        with transaction.atomic():
            return Cuenta.objects.create(**datos)

    # --- Destinatario / Proveedor
    def test_cuit_destinatario_formato(self):
        for cuit in ("1" * 10, "1" * 12, "2000000000a"):
            with self.assertRaises((IntegrityError, DataError)), transaction.atomic():
                Destinatario.objects.create(nombre="X", tipo="clinica", cuit=cuit)
        Destinatario.objects.create(nombre="X", tipo="clinica", cuit="1" * 11)

    def test_cuit_destinatario_unico_y_null_repetido_ok(self):
        with self.assertRaises((IntegrityError, DataError)), transaction.atomic():
            Destinatario.objects.create(nombre="X", tipo="clinica", cuit="20000000001")
        Destinatario.objects.create(nombre="A", tipo="clinica")
        Destinatario.objects.create(nombre="B", tipo="clinica")

    def test_proveedor_nombre_unico_sin_mayusculas(self):
        with self.assertRaises((IntegrityError, DataError)), transaction.atomic():
            Proveedor.objects.create(nombre="MERCADOPAGO")

    # --- montos
    def test_monto_negativo(self):
        self.falla(self.transf(minimo_por_pago=D("-0.01")), "cuenta_montos_no_negativos")

    def test_monto_cero_semanal_ok_y_minimo_cero_ok(self):
        self.anda(self.transf(tope_semanal=D("0.00"), minimo_por_pago=D("0.00")))

    # --- transferencia
    def test_transferencia_campos_nulos(self):
        for campo in ("tope_mensual", "tope_semanal", "minimo_por_pago"):
            self.falla(self.transf(**{campo: None}), "cuenta_transferencia_campos")

    def test_transferencia_con_proveedor_u_objetivo(self):
        self.falla(self.transf(proveedor=self.prov), "cuenta_transferencia_campos")
        self.falla(self.transf(objetivo=D("1.00")), "cuenta_transferencia_campos")

    def test_transferencia_sin_alias_ni_cbu(self):
        self.falla(self.transf(alias=None), "cuenta_transferencia_alias_o_cbu")
        self.anda(self.transf(alias=None, cbu=CBU))

    def test_transferencia_tope_mensual_cero(self):
        self.falla(
            self.transf(tope_mensual=D("0.00"), tope_semanal=D("0.00"), minimo_por_pago=D("0.00")),
            "cuenta_transferencia_tope_positivo",
        )
        self.anda(
            self.transf(tope_mensual=D("0.01"), tope_semanal=D("0.00"), minimo_por_pago=D("0.01"))
        )

    def test_transferencia_minimo_contra_tope_mensual(self):
        self.falla(
            self.transf(minimo_por_pago=D("1000.01"), tope_semanal=D("0.00")),
            "cuenta_transferencia_minimo",
        )
        self.anda(self.transf(minimo_por_pago=D("1000.00"), tope_semanal=D("0.00")))
        self.anda(self.transf(alias="a2", minimo_por_pago=D("999.99"), tope_semanal=D("0.00")))

    def test_transferencia_semanal_contra_tope_mensual(self):
        self.falla(self.transf(tope_semanal=D("1000.01")), "cuenta_transferencia_semanal_tope")
        self.anda(self.transf(tope_semanal=D("1000.00")))
        self.anda(self.transf(alias="a2", tope_semanal=D("999.99")))

    def test_transferencia_semanal_contra_minimo(self):
        self.falla(self.transf(tope_semanal=D("99.99")), "cuenta_transferencia_semanal_minimo")
        self.anda(self.transf(tope_semanal=D("100.00")))
        self.anda(self.transf(alias="a2", tope_semanal=D("100.01")))
        self.anda(self.transf(alias="a3", tope_semanal=D("0.00")))

    # --- POS
    def test_pos_campos(self):
        self.falla(self.pos(proveedor=None), "cuenta_pos_campos")
        self.falla(self.pos(objetivo=None), "cuenta_pos_campos")
        self.falla(self.pos(alias="x"), "cuenta_pos_campos")
        self.falla(self.pos(cbu=CBU), "cuenta_pos_campos")
        self.falla(self.pos(titular_cuit="1" * 11), "cuenta_pos_campos")
        self.falla(self.pos(tope_mensual=D("1.00")), "cuenta_pos_campos")
        self.falla(self.pos(banco="Banco"), "cuenta_pos_campos")
        self.anda(self.pos())

    def test_pos_objetivo_positivo(self):
        self.falla(self.pos(objetivo=D("0.00")), "cuenta_pos_objetivo_positivo")
        self.anda(self.pos(objetivo=D("0.01")))

    # --- efectivo
    def test_efectivo_campos(self):
        self.falla(self.efectivo(tope_mensual=D("1.00")), "cuenta_efectivo_campos")
        self.falla(self.efectivo(objetivo=D("1.00")), "cuenta_efectivo_campos")
        self.falla(self.efectivo(proveedor=self.prov), "cuenta_efectivo_campos")
        self.falla(self.efectivo(alias="x"), "cuenta_efectivo_campos")
        self.falla(self.efectivo(banco="B"), "cuenta_efectivo_campos")
        self.anda(self.efectivo())

    def test_efectivo_activa_unica_y_otra_inactiva_ok(self):
        self.anda(self.efectivo())
        self.falla(self.efectivo(), "cuenta_efectivo_activa_unica")
        self.anda(self.efectivo(estado="inactiva"))
        self.anda(self.efectivo(estado="archivada"))

    # --- formatos y unicidad
    def test_cbu_formato(self):
        for cbu in ("0" * 21, "0" * 23, "0" * 21 + "a"):
            self.falla(self.transf(cbu=cbu), "cuenta_cbu_formato")
        self.anda(self.transf(cbu=CBU))

    def test_titular_cuit_formato(self):
        for cuit in ("1" * 10, "1" * 12, "2000000000a"):
            self.falla(self.transf(titular_cuit=cuit), "cuenta_titular_cuit_formato")
        self.anda(self.transf(titular_cuit="1" * 11))

    def test_alias_unico_sin_mayusculas(self):
        self.anda(self.transf(alias="Alias.Uno"))
        self.falla(self.transf(alias="alias.uno"), "cuenta_alias_unico")

    def test_alias_unico_aun_archivada(self):
        self.anda(self.transf(alias="x.y", estado="archivada"))
        self.falla(self.transf(alias="X.Y"), "cuenta_alias_unico")

    def test_cbu_unico(self):
        self.anda(self.transf(cbu=CBU))
        self.falla(self.transf(alias="otro", cbu=CBU), "cuenta_cbu_unico")
