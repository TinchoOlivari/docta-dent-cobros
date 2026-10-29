from datetime import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction

from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta, Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.models import Sesion, Terminal


def h(hora, minuto=0, segundo=0):
    return datetime(2026, 10, 14, hora, minuto, segundo, tzinfo=ARG)


class ConstraintsTerminalTest(SistemaTestCase):
    def setUp(self):
        self.prov = Proveedor.objects.get(nombre="Mercadopago")
        d = Destinatario.objects.create(nombre="Rodrigo", tipo="clinica")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=d,
            titular="Titular Test",
            proveedor=self.prov,
            objetivo=Decimal("300000.00"),
        )
        self.t = self.terminal("Point 1")

    def terminal(self, nombre, **kw):
        kw.setdefault("acepta_debito", True)
        return Terminal.objects.create(nombre=nombre, proveedor=self.prov, **kw)

    def sesion(self, inicio, fin, terminal=None):
        return Sesion.objects.create(
            terminal=terminal or self.t, cuenta=self.cuenta, inicio=inicio, fin=fin
        )

    def falla(self, **kw):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.sesion(**kw)

    def test_nombre_repetido_con_otra_mayuscula(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.terminal("POINT 1")

    def test_terminal_sin_metodos(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.terminal("Sin", acepta_debito=False)

    def test_sesion_con_fin_igual_al_inicio(self):
        self.falla(inicio=h(9), fin=h(9))

    def test_sesion_con_fin_un_segundo_despues_entra(self):
        self.sesion(h(9), h(9, 0, 1))

    def test_dos_sesiones_abiertas_de_la_misma_terminal(self):
        self.sesion(h(9), None)
        self.falla(inicio=h(10), fin=None)

    def test_sesiones_superpuestas(self):
        self.sesion(h(9), h(12))
        self.falla(inicio=h(11), fin=h(13))

    def test_superposicion_de_un_segundo(self):
        self.sesion(h(9), h(12))
        self.falla(inicio=h(11, 59, 59), fin=h(13))

    def test_sesiones_seguidas_entran(self):
        self.sesion(h(9), h(12))
        self.sesion(h(12), h(14))

    def test_misma_franja_en_dos_terminales_entra(self):
        self.sesion(h(9), h(12))
        self.sesion(h(9), h(12), terminal=self.terminal("Point 2"))

    def test_cuenta_pos_sin_acumula_desde(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cuenta.objects.filter(pk=self.cuenta.pk).update(acumula_desde=None)

    def test_cuenta_no_pos_con_acumula_desde(self):
        d = Destinatario.objects.create(nombre="Jimena", tipo="clinica")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cuenta.objects.create(destinatario=d, tipo="efectivo", acumula_desde=h(9))

    def test_sesion_se_puede_borrar(self):
        self.sesion(h(9), h(12))
        Sesion.objects.get().delete()
        self.assertFalse(Sesion.objects.exists())
