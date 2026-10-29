from datetime import date, datetime
from decimal import Decimal

from payment_instructions.models import User
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Proveedor
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
OCT = date(2026, 10, 1)
EN_OCT = datetime(2026, 10, 15, 12, 0, tzinfo=ARG)


class DatosMartinO:
    """Mixin: destinatario "Destinatario Ejemplo" con 2 cuentas de transferencia y 2 de terminal POS."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        self.martin = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo", tipo="profesional"
        )
        self.tincho = self.transf(self.martin, "alias.uno", "100000.00")
        self.galicia = self.transf(self.martin, "alias.dos", "500000.00")
        self.mp = self.pos(self.martin, "Martin MP", "Mercadopago", "300000.00")
        self.payway = self.pos(self.martin, "Martin Payway", "Payway", "250000.00")

    def transf(self, dest, alias, tope):
        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias=alias,
            tope_mensual=D(tope),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )

    def pos(self, dest, titular, proveedor, objetivo):
        return crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=dest,
            titular=titular,
            proveedor=Proveedor.objects.get(nombre=proveedor),
            objetivo=D(objetivo),
        )

    def cobro(self, cuenta, monto, fecha=EN_OCT, estado="finalizado", metodo=None, **kw):
        metodo = metodo or ("transferencia" if cuenta.tipo == "transferencia" else "debito")
        kw.setdefault("destinatario", self.martin)
        return cobro_prueba(
            cuenta=cuenta, monto=D(monto), fecha=fecha, estado=estado, metodo=metodo, **kw
        )

    def cargar_octubre(self):
        self.cobro(self.tincho, "100000.00")
        self.cobro(self.galicia, "400000.00")
        self.cobro(self.mp, "300000.00")
        self.cobro(self.payway, "200000.00")
