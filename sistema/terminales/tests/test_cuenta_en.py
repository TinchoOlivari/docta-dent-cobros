from django.core.exceptions import ValidationError

from sistema.terminales.models import Terminal
from sistema.terminales.servicios import (
    abrir_sesion,
    cambiar_cuenta,
    cerrar_sesion,
    crear_terminal,
    cuenta_en,
    cuenta_para_cobro,
    terminales_disponibles,
)
from sistema.terminales.tests.test_sesion import SesionBase, h


class CuentaEnTest(SesionBase):
    ahora = h(18)

    def setUp(self):
        super().setUp()
        self.s_a = self.abrir(self.cuenta_a, h(9))
        cambiar_cuenta(self.terminal, self.cuenta_b, h(12), usuario=None)

    def test_us3_1_un_segundo_antes_del_cambio_es_la_cuenta_anterior(self):
        self.assertEqual(cuenta_en(self.terminal, h(11, 59, 59)), self.cuenta_a)

    def test_us3_2_justo_en_el_cambio_es_la_cuenta_nueva(self):
        self.assertEqual(cuenta_en(self.terminal, h(12)), self.cuenta_b)

    def test_antes_del_inicio_no_hay_cuenta(self):
        self.assertIsNone(cuenta_en(self.terminal, h(8, 59, 59)))
        self.assertEqual(cuenta_en(self.terminal, h(9)), self.cuenta_a)

    def test_sesion_abierta_cubre_el_futuro(self):
        from datetime import timedelta

        self.assertEqual(cuenta_en(self.terminal, h(18) + timedelta(days=1)), self.cuenta_b)

    def test_hueco_entre_sesiones_no_tiene_cuenta(self):
        sesion = self.terminal.sesiones.get(fin__isnull=True)
        cerrar_sesion(sesion, h(12, 30), usuario=None)
        abrir_sesion(self.terminal, self.cuenta_a, h(14), usuario=None)
        self.assertIsNone(cuenta_en(self.terminal, h(13)))
        self.assertIsNone(cuenta_en(self.terminal, h(14) - (h(14) - h(13, 59, 59))))
        self.assertEqual(cuenta_en(self.terminal, h(14)), self.cuenta_a)
        with self.assertRaisesMessage(
            ValidationError, "La terminal no tiene una sesión en esa fecha y hora."
        ):
            cuenta_para_cobro(self.terminal, h(13))

    def test_cuenta_para_cobro_devuelve_la_cuenta_de_la_sesion(self):
        self.assertEqual(cuenta_para_cobro(self.terminal, h(11, 59, 59)), self.cuenta_a)
        self.assertEqual(cuenta_para_cobro(self.terminal, h(12)), self.cuenta_b)


class TerminalesDisponiblesTest(SesionBase):
    def test_us3_3_sin_sesion_abierta_no_aparece(self):
        crear_terminal(usuario=None, nombre="Payway #1", proveedor=self.payway, acepta_debito=True)
        self.abrir()
        self.assertEqual(list(terminales_disponibles("debito")), [self.terminal])

    def test_sesion_cerrada_no_cuenta(self):
        cerrar_sesion(self.abrir(), h(9, 30), usuario=None)
        self.assertEqual(list(terminales_disponibles("debito")), [])

    def test_terminal_inactiva_sin_sesion_no_aparece(self):
        self.terminal.estado = Terminal.Estado.INACTIVA
        self.terminal.save()
        self.assertEqual(list(terminales_disponibles("debito")), [])

    def test_terminal_sin_qr_no_aparece_con_qr(self):
        self.abrir()
        self.assertEqual(list(terminales_disponibles("qr")), [])

    def test_us3_4_fr32_str_es_solo_el_nombre(self):
        self.abrir()
        self.assertEqual(str(terminales_disponibles("debito").get()), "Point Smart #1")

    def test_orden_por_nombre_y_sin_duplicados(self):
        otra = crear_terminal(
            usuario=None, nombre="Alfa #1", proveedor=self.payway, acepta_debito=True
        )
        self.abrir()
        cuenta_p = self.pos(self.rodrigo, self.payway)
        abrir_sesion(otra, cuenta_p, h(9), usuario=None)
        self.assertEqual(
            [t.nombre for t in terminales_disponibles("debito")], ["Alfa #1", "Point Smart #1"]
        )

    def test_metodo_fuera_de_lista_es_vacio(self):
        self.abrir()
        self.assertEqual(list(terminales_disponibles("efectivo")), [])
