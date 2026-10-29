from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError

from sistema.asignacion.servicios import lista_con_cupo
from sistema.cobros import servicios
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_efectivo import DELAY, PACIENTE, BaseEfectivo, item
from sistema.cobros.tests.test_transferencia import BaseTransferencia
from sistema.dentalink.cliente import DentalinkNoDisponible

D = Decimal


class FaltanItemsBase:
    def faltan(self, metodo="efectivo", monto="50000.00", nombre="Juan Paciente", **kw):
        with mock.patch(PACIENTE, side_effect=DentalinkNoDisponible) as paciente:
            cobro = servicios.crear_cobro(
                usuario=self.ana,
                metodo=metodo,
                monto=D(monto),
                nombre_paciente=nombre,
                **kw,
            )
        paciente.assert_not_called()
        return cobro

    def completar(self, cobro, items):
        return servicios.completar_items(cobro, usuario=self.ana, id_paciente=501, items=items)


class AltaFaltanItemsTest(FaltanItemsBase, BaseEfectivo):
    def test_us5_1_efectivo_queda_finalizado_con_la_marca_y_sin_envio(self):
        with mock.patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            cobro = self.faltan()
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")
        self.assertTrue(cobro.faltan_items)
        self.assertEqual(cobro.nombre_paciente, "Juan Paciente")
        self.assertIsNone(cobro.id_paciente_dentalink)
        self.assertEqual(cobro.items.count(), 0)
        delay.assert_not_called()

    def test_nombre_vacio_se_rechaza(self):
        for nombre in ("", "   "):
            with self.assertRaisesMessage(ValidationError, "Escribí el nombre del paciente."):
                self.faltan(nombre=nombre)

    def test_con_items_se_rechaza(self):
        with self.assertRaises(ValidationError):
            self.faltan(items=[item(1, "50000.00")])

    def test_monto_cero_se_rechaza(self):
        with self.assertRaisesMessage(ValidationError, "El monto debe ser mayor que $0,00."):
            self.faltan(monto="0.00")

    def test_monto_de_un_centavo_se_acepta_sin_regla_de_suma(self):
        self.assertEqual(self.faltan(monto="0.01").monto, D("0.01"))


class CompletarItemsTest(FaltanItemsBase, BaseEfectivo):
    def test_us5_2_suma_menor_se_rechaza_y_suma_exacta_quita_la_marca(self):
        cobro = self.faltan()
        with self.assertRaisesMessage(
            ValidationError, "Los ítems suman $45.000,00 y el cobro es de $50.000,00."
        ):
            self.completar(cobro, [item(1, "40000.00"), item(2, "5000.00")])
        cobro.refresh_from_db()
        self.assertTrue(cobro.faltan_items)
        with mock.patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            self.completar(cobro, [item(1, "40000.00"), item(2, "10000.00")])
        cobro.refresh_from_db()
        self.assertFalse(cobro.faltan_items)
        self.assertEqual(cobro.items.count(), 2)
        delay.assert_called_once_with(cobro.pk)

    def test_suma_mayor_se_rechaza(self):
        cobro = self.faltan()
        with self.assertRaisesMessage(ValidationError, "Los ítems suman $50.000,01"):
            self.completar(cobro, [item(1, "40000.00"), item(2, "10000.01")])

    def test_toma_paciente_nombre_y_dni_de_dentalink(self):
        cobro = self.faltan(nombre="Juan P.")
        self.completar(cobro, [item(1, "40000.00"), item(2, "10000.00")])
        cobro.refresh_from_db()
        self.assertEqual(
            (cobro.id_paciente_dentalink, cobro.nombre_paciente, cobro.dni_paciente),
            (501, "Juan Paciente", "30111222"),
        )

    def test_sin_la_marca_se_rechaza(self):
        cobro = self.crear()
        with self.assertRaisesMessage(
            ValidationError, "El cobro no está «Sin vincular a Dentalink»."
        ):
            self.completar(cobro, [item(1, "40000.00"), item(2, "25000.00")])

    def test_cancelado_se_rechaza(self):
        cobro = self.faltan()
        servicios.cancelar_cobro(
            cobro,
            usuario=self.admin_o_ana(),
            motivo="datos_erroneos",
            estado_visto="finalizado",
        )
        with self.assertRaises(ValidationError):
            self.completar(cobro, [item(1, "40000.00"), item(2, "10000.00")])

    def admin_o_ana(self):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )


class TransferenciaFaltanItemsTest(FaltanItemsBase, BaseTransferencia):
    def test_us5_3_queda_pendiente_en_la_cuenta_y_reserva_cupo(self):
        cobro = self.faltan("transferencia")
        self.assertEqual(cobro.estado, "pendiente")
        self.assertEqual(cobro.cuenta, self.cuenta)
        self.assertTrue(cobro.faltan_items)
        self.assertEqual(lista_con_cupo()[0].cupo_mensual, D("10000.00"))

    def test_completada_no_envia_hasta_adjuntar(self):
        cobro = self.faltan("transferencia")
        with mock.patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            self.completar(cobro, [item(1, "40000.00"), item(2, "10000.00")])
            delay.assert_not_called()
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        delay.assert_called_once_with(cobro.pk)
