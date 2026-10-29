from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from sistema.auditoria.servicios import ENTIDADES
from sistema.cobros.models import PagoComplementario
from sistema.cobros.tests.fabrica import pago_prueba
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Proveedor
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
OCT = date(2026, 10, 1)
# 30/11/2026 22:00 ARG = 01/12/2026 01:00 UTC
AHORA = datetime(2026, 11, 30, 22, 0, tzinfo=ARG)


class ModeloPagoComplementarioTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("django.utils.timezone.now", return_value=AHORA)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = get_user_model().objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        self.dest = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        self.otro = crear_destinatario(usuario=None, nombre="Rodrigo", tipo="profesional")
        self.cuenta = self.transf(self.dest, "jimena.roble.rana")

    def transf(self, dest, alias, **kw):
        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias=alias,
            tope_mensual=D("600000.00"),
            **kw,
        )

    def crear(self, **kw):
        base = dict(
            destinatario=self.dest,
            cuenta=self.cuenta,
            mes=OCT,
            monto=D("30000.00"),
            metodo="transferencia",
            creado_por=self.admin,
        )
        base.update(kw)
        return pago_prueba(**base)

    def falla(self, **kw):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.crear(**kw)

    def pago(self, **kw):
        base = dict(
            destinatario=self.dest,
            cuenta=self.cuenta,
            mes=OCT,
            monto=D("30000.00"),
            metodo="transferencia",
            creado_por=self.admin,
        )
        base.update(kw)
        return PagoComplementario(**base)

    def test_constraints_rechazan_datos_invalidos(self):
        self.falla(monto=D("0.00"))
        self.falla(cuenta=None)
        self.falla(metodo="efectivo")
        self.falla(mes=date(2026, 10, 2))
        self.falla(estado="anulado", motivo_anulacion="")
        self.falla(motivo_anulacion="datos_erroneos")
        self.falla(estado="anulado", motivo_anulacion="otro", detalle_anulacion="")

    def test_monto_minimo_y_efectivo_sin_cuenta_se_guardan(self):
        self.assertEqual(self.crear(monto=D("0.01")).monto, D("0.01"))
        self.assertIsNotNone(self.crear(metodo="efectivo", cuenta=None).pk)
        self.crear(estado="anulado", motivo_anulacion="otro", detalle_anulacion="x")

    def test_clean_cuenta_de_terminal_pos(self):
        pos = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=self.dest,
            titular="Titular Test",
            proveedor=Proveedor.objects.get(nombre="Mercadopago"),
            objetivo=D("100000.00"),
        )
        with self.assertRaisesMessage(
            ValidationError, "La cuenta debe ser una cuenta de transferencia."
        ):
            self.pago(cuenta=pos).full_clean()

    def test_clean_cuenta_de_otro_destinatario(self):
        ajena = self.transf(self.otro, "rodrigo.mp")
        with self.assertRaisesMessage(ValidationError, "La cuenta debe ser del destinatario"):
            self.pago(cuenta=ajena).full_clean()

    def test_clean_mes_posterior_al_corriente(self):
        # 01/12 01:00 UTC pero 30/11 en Argentina: el mes corriente es noviembre.
        with self.assertRaisesMessage(
            ValidationError, "El mes no puede ser posterior al mes corriente."
        ):
            self.pago(mes=date(2026, 12, 1)).full_clean()
        self.pago(mes=date(2026, 11, 1)).full_clean()
        self.pago(mes=date(2026, 9, 1)).full_clean()

    def test_clean_cuenta_inactiva_o_archivada_pasa(self):
        for estado in ("inactiva", "archivada"):
            cuenta = self.transf(self.dest, f"alias.{estado}", estado=estado)
            self.pago(cuenta=cuenta).full_clean()

    def test_no_se_borra(self):
        pago = self.crear()
        with self.assertRaises(ProtectedError), transaction.atomic():
            pago.delete()
        # QuerySet.delete() también: Registro bloquea el borrado.
        with self.assertRaises(ProtectedError), transaction.atomic():
            PagoComplementario.objects.all().delete()

    def test_auditoria_y_sin_paciente(self):
        self.assertIn("cobros.pagocomplementario", ENTIDADES)
        nombres = {f.name for f in PagoComplementario._meta.get_fields()}
        for ausente in ("paciente", "profesional", "items"):
            self.assertNotIn(ausente, nombres)
