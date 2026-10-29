import itertools
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.utils import timezone

from payment_instructions.models import (
    Banco,
    CuentaPosnet,
    Payment,
    Posnet,
    TarjetaPaymentDetail,
    User,
)
from payment_instructions.tests import BaseTestCase


class TarjetaPaymentTestMixin(BaseTestCase):
    """Helpers comunes para crear bancos, posnets, cuentas y pagos con tarjeta."""

    def setUp(self):
        self.professional = User.objects.create_user(
            username="professional1",
            email="prof@example.com",
            password="pass",
            role=User.PROFESSIONAL,
        )
        self.operator = User.objects.create_user(
            username="operator1", email="op@example.com", password="pass", role=User.OPERATOR
        )
        self._seq = itertools.count(1)

    def _create_banco(self, is_active=True, nombre=None):
        nombre = nombre or f"Banco {next(self._seq)}"
        return Banco.objects.create(nombre=nombre, is_active=is_active)

    def _create_posnet(self, is_active=True, cuenta_actual=None, nombre=None):
        nombre = nombre or f"Posnet {next(self._seq)}"
        return Posnet.objects.create(
            nombre=nombre,
            proveedor=Posnet.MERCADOPAGO,
            is_active=is_active,
            cuenta_actual=cuenta_actual,
        )

    def _create_cuenta(self, objetivo_monto=0, acumulado_desde=None, nombre=None):
        nombre = nombre or f"Cuenta {next(self._seq)}"
        kwargs = {"nombre": nombre, "objetivo_monto": objetivo_monto}
        if acumulado_desde is not None:
            kwargs["acumulado_desde"] = acumulado_desde
        return CuentaPosnet.objects.create(**kwargs)

    def _create_payment_tarjeta(self, amount=1000, estado=Payment.COMPLETADO, created_at=None):
        """Payment.objects.create() no llama a clean(): igual que el resto de los tests
        de este repo, se usa para armar el estado exacto que necesita cada caso."""
        payment = Payment.objects.create(
            metodo=Payment.POSNET_TARJETA,
            payment_type=Payment.REGULAR,
            amount=amount,
            professional=self.professional,
            operator_user=self.operator,
            patient_name="Paciente de Prueba",
            estado=estado,
        )
        if created_at is not None:
            payment.created_at = created_at
            payment.save(update_fields=["created_at"])
        return payment

    def _create_detalle_tarjeta(self, payment, posnet, cuenta, banco=None, monto_cobrado=None):
        banco = banco or self._create_banco()
        return TarjetaPaymentDetail.objects.create(
            payment=payment,
            tipo_tarjeta=TarjetaPaymentDetail.CREDITO,
            banco=banco,
            ultimos_4_digitos="1234",
            posnet=posnet,
            cuenta=cuenta,
            numero_operacion=f"OP{payment.pk}",
            monto_cobrado=monto_cobrado if monto_cobrado is not None else payment.amount,
        )


class TarjetaPaymentDetailCleanTests(TarjetaPaymentTestMixin):
    def _build_valid_detail(self, **overrides):
        payment = overrides.pop("payment", None) or self._create_payment_tarjeta(amount=1000)
        posnet = overrides.pop("posnet", None) or self._create_posnet()
        cuenta = overrides.pop("cuenta", None) or self._create_cuenta()
        banco = overrides.pop("banco", None) or self._create_banco()
        defaults = {
            "payment": payment,
            "tipo_tarjeta": TarjetaPaymentDetail.CREDITO,
            "banco": banco,
            "ultimos_4_digitos": "1234",
            "posnet": posnet,
            "cuenta": cuenta,
            "numero_operacion": "OP1",
            "monto_cobrado": 1000,
        }
        defaults.update(overrides)
        return TarjetaPaymentDetail(**defaults)

    def test_ultimos_4_digitos_con_letras_rechaza(self):
        detalle = self._build_valid_detail(ultimos_4_digitos="12a4")
        with self.assertRaises(ValidationError) as cm:
            detalle.full_clean()
        self.assertIn("ultimos_4_digitos", cm.exception.message_dict)

    def test_ultimos_4_digitos_con_largo_distinto_rechaza(self):
        detalle = self._build_valid_detail(ultimos_4_digitos="123")
        with self.assertRaises(ValidationError) as cm:
            detalle.full_clean()
        self.assertIn("ultimos_4_digitos", cm.exception.message_dict)

    def test_banco_inactivo_rechaza(self):
        banco_inactivo = self._create_banco(is_active=False)
        detalle = self._build_valid_detail(banco=banco_inactivo)
        with self.assertRaises(ValidationError) as cm:
            detalle.full_clean()
        self.assertIn("banco", cm.exception.message_dict)

    def test_posnet_inactivo_rechaza(self):
        posnet_inactivo = self._create_posnet(is_active=False)
        detalle = self._build_valid_detail(posnet=posnet_inactivo)
        with self.assertRaises(ValidationError) as cm:
            detalle.full_clean()
        self.assertIn("posnet", cm.exception.message_dict)

    def test_monto_cobrado_menor_al_monto_del_pago_rechaza(self):
        payment = self._create_payment_tarjeta(amount=1000)
        detalle = self._build_valid_detail(payment=payment, monto_cobrado=999)
        with self.assertRaises(ValidationError) as cm:
            detalle.full_clean()
        self.assertIn("monto_cobrado", cm.exception.message_dict)

    def test_monto_cobrado_igual_al_monto_del_pago_no_rechaza(self):
        payment = self._create_payment_tarjeta(amount=1000)
        detalle = self._build_valid_detail(payment=payment, monto_cobrado=1000)
        detalle.full_clean()

    def test_caso_valido_no_rechaza(self):
        detalle = self._build_valid_detail()
        detalle.full_clean()

    def test_tipo_tarjeta_invalido_rechaza_con_clean_directo(self):
        # .clean() a secas, no full_clean(): un caller directo (shell, admin bypass)
        # no pasa por la validación de choices del campo, que solo corre en full_clean().
        detalle = self._build_valid_detail(tipo_tarjeta="INVENTADO")
        with self.assertRaises(ValidationError) as cm:
            detalle.clean()
        self.assertIn("tipo_tarjeta", cm.exception.message_dict)

    def test_numero_operacion_vacio_rechaza_con_clean_directo(self):
        detalle = self._build_valid_detail(numero_operacion="")
        with self.assertRaises(ValidationError) as cm:
            detalle.clean()
        self.assertIn("numero_operacion", cm.exception.message_dict)


class TarjetaPaymentDetailUniqueConstraintTests(TarjetaPaymentTestMixin):
    """Mismo posnet + mismo número de operación es el mismo cobro físico. La
    constraint es la última barrera contra un reenvío que ya pasó las validaciones
    de la view (doble click, timeout con retry)."""

    def test_mismo_posnet_y_numero_operacion_rechaza(self):
        posnet = self._create_posnet()
        cuenta = self._create_cuenta()
        banco = self._create_banco()
        payment1 = self._create_payment_tarjeta()
        payment2 = self._create_payment_tarjeta()
        self._create_detalle_tarjeta(payment1, posnet, cuenta, banco=banco)
        with self.assertRaises(IntegrityError):
            with db_transaction.atomic():  # Postgres aborta la transacción: sub-savepoint
                TarjetaPaymentDetail.objects.create(
                    payment=payment2,
                    tipo_tarjeta=TarjetaPaymentDetail.CREDITO,
                    banco=banco,
                    ultimos_4_digitos="1234",
                    posnet=posnet,
                    cuenta=cuenta,
                    numero_operacion=f"OP{payment1.pk}",  # igual al de payment1, a propósito
                    monto_cobrado=1000,
                )

    def test_mismo_numero_operacion_en_otro_posnet_no_rechaza(self):
        cuenta = self._create_cuenta()
        banco = self._create_banco()
        payment1 = self._create_payment_tarjeta()
        payment2 = self._create_payment_tarjeta()
        self._create_detalle_tarjeta(payment1, self._create_posnet(), cuenta, banco=banco)
        TarjetaPaymentDetail.objects.create(
            payment=payment2,
            tipo_tarjeta=TarjetaPaymentDetail.CREDITO,
            banco=banco,
            ultimos_4_digitos="1234",
            posnet=self._create_posnet(),  # otro posnet
            cuenta=cuenta,
            numero_operacion=f"OP{payment1.pk}",  # mismo número, otro posnet
            monto_cobrado=1000,
        )


class PosnetSaveTests(TarjetaPaymentTestMixin):
    def test_crear_con_cuenta_actual_setea_cuenta_actual_desde(self):
        cuenta = self._create_cuenta()
        posnet = self._create_posnet(cuenta_actual=cuenta)
        self.assertIsNotNone(posnet.cuenta_actual_desde)

    def test_guardar_sin_cambiar_cuenta_actual_no_cambia_la_fecha(self):
        cuenta = self._create_cuenta()
        posnet = self._create_posnet(cuenta_actual=cuenta)
        fecha_original = posnet.cuenta_actual_desde

        posnet.is_active = False
        posnet.save()
        posnet.refresh_from_db()

        self.assertEqual(posnet.cuenta_actual_desde, fecha_original)

    def test_cambiar_cuenta_actual_cambia_la_fecha(self):
        cuenta1 = self._create_cuenta()
        cuenta2 = self._create_cuenta()
        posnet = self._create_posnet(cuenta_actual=cuenta1)
        fecha_original = posnet.cuenta_actual_desde

        posnet.cuenta_actual = cuenta2
        posnet.save()
        posnet.refresh_from_db()

        self.assertNotEqual(posnet.cuenta_actual_desde, fecha_original)


class CuentaPosnetAcumuladoTests(TarjetaPaymentTestMixin):
    def test_sin_pagos_acumulado_es_cero(self):
        cuenta = self._create_cuenta(objetivo_monto=0)
        self.assertEqual(cuenta.get_acumulado_recibido(), 0)

    def test_sin_objetivo_definido_porcentaje_es_none(self):
        cuenta = self._create_cuenta(objetivo_monto=0)
        self.assertIsNone(cuenta.get_porcentaje_objetivo())

    def test_pago_exactamente_en_el_objetivo_da_100(self):
        cuenta = self._create_cuenta(objetivo_monto=1000)
        posnet = self._create_posnet(cuenta_actual=cuenta)
        payment = self._create_payment_tarjeta(amount=1000)
        self._create_detalle_tarjeta(payment, posnet, cuenta)

        self.assertEqual(cuenta.get_acumulado_recibido(), 1000)
        self.assertEqual(cuenta.get_porcentaje_objetivo(), 100)

    def test_pago_arriba_del_objetivo_da_mas_de_100(self):
        cuenta = self._create_cuenta(objetivo_monto=1000)
        posnet = self._create_posnet(cuenta_actual=cuenta)
        payment = self._create_payment_tarjeta(amount=1500)
        self._create_detalle_tarjeta(payment, posnet, cuenta)

        self.assertGreater(cuenta.get_porcentaje_objetivo(), 100)

    def test_pago_abajo_del_objetivo_da_menos_de_100(self):
        cuenta = self._create_cuenta(objetivo_monto=1000)
        posnet = self._create_posnet(cuenta_actual=cuenta)
        payment = self._create_payment_tarjeta(amount=500)
        self._create_detalle_tarjeta(payment, posnet, cuenta)

        self.assertLess(cuenta.get_porcentaje_objetivo(), 100)

    def test_pago_cancelado_no_cuenta(self):
        cuenta = self._create_cuenta(objetivo_monto=1000)
        posnet = self._create_posnet(cuenta_actual=cuenta)
        pago_completado = self._create_payment_tarjeta(amount=300, estado=Payment.COMPLETADO)
        self._create_detalle_tarjeta(pago_completado, posnet, cuenta)
        pago_cancelado = self._create_payment_tarjeta(amount=700, estado=Payment.CANCELADO)
        self._create_detalle_tarjeta(pago_cancelado, posnet, cuenta)

        self.assertEqual(cuenta.get_acumulado_recibido(), 300)

    def test_pago_anterior_a_acumulado_desde_no_cuenta(self):
        ahora = timezone.now()
        cuenta = self._create_cuenta(objetivo_monto=1000, acumulado_desde=ahora)
        posnet = self._create_posnet(cuenta_actual=cuenta)
        payment = self._create_payment_tarjeta(amount=1000, created_at=ahora - timedelta(days=1))
        self._create_detalle_tarjeta(payment, posnet, cuenta)

        self.assertEqual(cuenta.get_acumulado_recibido(), 0)
