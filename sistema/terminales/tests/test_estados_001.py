from decimal import Decimal

from django.core.exceptions import ValidationError

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import (
    cambiar_estado_cuenta,
    cambiar_estado_destinatario,
    desactivar_cuentas,
    editar_cuenta,
)
from sistema.terminales.models import Sesion
from sistema.terminales.servicios import cerrar_sesion
from sistema.terminales.tests.test_sesion import SesionBase, h

MSG_CUENTA = "La cuenta tiene una sesión abierta en una terminal POS. Cerrá la sesión primero."
MSG_DEST = (
    "El destinatario tiene una cuenta con una sesión abierta en una terminal POS. "
    "Cerrá la sesión primero."
)


class EstadosConSesionAbiertaTest(SesionBase):
    def setUp(self):
        super().setUp()
        self.sesion = self.abrir()  # cuenta_a (Rodrigo) abierta

    def test_destinatario_con_sesion_abierta_no_pasa_a_inactivo_ni_archivado(self):
        for estado in (Destinatario.Estado.INACTIVO, Destinatario.Estado.ARCHIVADO):
            with self.assertRaisesMessage(ValidationError, MSG_DEST):
                cambiar_estado_destinatario(self.rodrigo, estado, usuario=None)
        self.rodrigo.refresh_from_db()
        self.assertEqual(self.rodrigo.estado, Destinatario.Estado.ACTIVO)

    def test_destinatario_sin_sesion_abierta_cambia_aunque_otro_tenga(self):
        cambiar_estado_destinatario(self.jimena, Destinatario.Estado.INACTIVO, usuario=None)
        self.jimena.refresh_from_db()
        self.assertEqual(self.jimena.estado, Destinatario.Estado.INACTIVO)

    def test_cuenta_con_sesion_abierta_no_pasa_a_inactiva_ni_archivada(self):
        for estado in (Cuenta.Estado.INACTIVA, Cuenta.Estado.ARCHIVADA):
            with self.assertRaisesMessage(ValidationError, MSG_CUENTA):
                cambiar_estado_cuenta(self.cuenta_a, estado, usuario=None)
        self.cuenta_a.refresh_from_db()
        self.assertEqual(self.cuenta_a.estado, Cuenta.Estado.ACTIVA)

    def test_cuenta_activa_a_activa_no_se_frena(self):
        cambiar_estado_cuenta(self.cuenta_a, Cuenta.Estado.ACTIVA, usuario=None)

    def test_desactivar_cuentas_con_una_sesion_abierta_no_cambia_ninguna(self):
        otra = self.pos(self.rodrigo)
        with self.assertRaisesMessage(ValidationError, MSG_CUENTA):
            desactivar_cuentas(self.rodrigo, usuario=None)
        for c in (self.cuenta_a, otra):
            c.refresh_from_db()
            self.assertEqual(c.estado, Cuenta.Estado.ACTIVA)

    def test_cerrada_la_sesion_el_cambio_entra(self):
        cerrar_sesion(self.sesion, h(9, 30), usuario=None)
        cambiar_estado_cuenta(self.cuenta_a, Cuenta.Estado.INACTIVA, usuario=None)
        cambiar_estado_destinatario(self.rodrigo, Destinatario.Estado.INACTIVO, usuario=None)
        self.cuenta_a.refresh_from_db()
        self.assertEqual(self.cuenta_a.estado, Cuenta.Estado.INACTIVA)

    def test_cuenta_con_sesion_abierta_no_pasa_a_destinatario_inactivo(self):
        cambiar_estado_destinatario(self.jimena, Destinatario.Estado.INACTIVO, usuario=None)
        with self.assertRaisesMessage(ValidationError, "El destinatario nuevo debe estar Activo."):
            editar_cuenta(self.cuenta_a, usuario=None, destinatario=self.jimena)
        self.cuenta_a.refresh_from_db()
        self.assertEqual(self.cuenta_a.destinatario, self.rodrigo)
        cerrar_sesion(self.sesion, h(10), usuario=None)
        editar_cuenta(self.cuenta_a, usuario=None, destinatario=self.jimena)
        self.cuenta_a.refresh_from_db()
        self.assertEqual(self.cuenta_a.destinatario, self.jimena)

    def test_cuenta_que_cambia_de_destinatario_conserva_sesion_y_cobros(self):
        cobro = cobro_prueba(
            cuenta=self.cuenta_a,
            terminal=self.terminal,
            destinatario=self.rodrigo,
            monto=Decimal("100.00"),
            estado="finalizado",
            metodo="debito",
            fecha=h(9, 30),
        )
        editar_cuenta(self.cuenta_a, usuario=None, destinatario=self.jimena)
        cobro.refresh_from_db()
        self.assertEqual(Sesion.objects.get().cuenta, self.cuenta_a)
        self.assertTrue(Sesion.objects.get().abierta)
        self.assertEqual(cobro.destinatario, self.rodrigo)
        with self.assertRaisesMessage(ValidationError, MSG_DEST):
            cambiar_estado_destinatario(self.jimena, Destinatario.Estado.INACTIVO, usuario=None)
