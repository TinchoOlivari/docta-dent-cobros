from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model

from sistema.core.tests import SistemaTestCase, SistemaTransactionTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta, Proveedor
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

from ..servicios import crear_regla

LUNES = date(2026, 10, 19)
MARTES = date(2026, 10, 20)


def en(dia, hhmm):
    h, m = hhmm.split(":")
    return datetime(dia.year, dia.month, dia.day, int(h), int(m), tzinfo=ARG)


class ReglasMixin:
    def setUp(self):
        super().setUp()
        patcher = patch("django.utils.timezone.now", return_value=en(LUNES, "07:00"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = get_user_model().objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        cuentas = {}
        for nombre, alias in [
            ("Destinatario Muestra", "alias.muestra"),
            ("Jimena", "jimena.roble.rana"),
            ("Rodrigo", "rodrigo.mp"),
            ("Martin P", "martin.piedra.casa"),
        ]:
            dest = crear_destinatario(usuario=None, nombre=nombre, tipo="profesional")
            cuentas[alias] = crear_cuenta(
                usuario=None,
                tipo="transferencia",
                destinatario=dest,
                alias=alias,
                tope_mensual=Decimal("500000.00"),
                tope_semanal=Decimal("0.00"),
                minimo_por_pago=Decimal("0.00"),
            )
        self.tincho = cuentas["alias.muestra"]
        self.jimena = cuentas["jimena.roble.rana"]
        self.rodrigo = cuentas["rodrigo.mp"]
        self.martin = cuentas["martin.piedra.casa"]
        dest_pos = crear_destinatario(usuario=None, nombre="Terminal", tipo="clinica")
        self.pos = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=dest_pos,
            titular="Titular Test",
            proveedor=Proveedor.objects.get(nombre="Mercadopago"),
            objetivo=Decimal("300000.00"),
        )

    def regla(self, cuando=None, **datos):
        datos.setdefault("cuenta", self.rodrigo)
        datos.setdefault("nombre", "Regla de prueba")
        datos.setdefault("disparador", "semana")
        datos.setdefault("dia", 1)
        datos.setdefault("hora", time(8, 0))
        datos.setdefault("accion", "primero")
        with patch("django.utils.timezone.now", return_value=cuando or en(LUNES, "07:00")):
            return crear_regla(usuario=self.admin, **datos)

    def lista(self):
        return list(
            Cuenta.objects.filter(posicion__isnull=False)
            .order_by("posicion")
            .values_list("alias", flat=True)
        )


class ReglasTestCase(ReglasMixin, SistemaTestCase):
    pass


class ReglasTransaccionTestCase(ReglasMixin, SistemaTransactionTestCase):
    """Para tests con hilos: cada hilo usa su propia conexión."""
