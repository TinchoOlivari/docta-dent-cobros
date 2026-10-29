from decimal import Decimal

from django.db import IntegrityError, connection, transaction

from django.core.exceptions import ValidationError

from sistema.asignacion.servicios import fila, mover_cuenta, sincronizar_lista
from sistema.asignacion.tests.test_asignar import DatosEjemplo
from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import (
    cambiar_estado_cuenta,
    cambiar_estado_destinatario,
    crear_cuenta,
    crear_destinatario,
    editar_cuenta,
)

D = Decimal


class ConstraintsPosicionTest(SistemaTestCase):
    def setUp(self):
        dest = Destinatario.objects.create(nombre="Test", tipo="clinica", cuit="20000000001")
        self.dest = dest
        self.a = self.transf("alias.uno")
        self.b = self.transf("alias.dos")

    def transf(self, alias, **kw):
        return Cuenta.objects.create(
            destinatario=self.dest,
            tipo="transferencia",
            alias=alias,
            tope_mensual=D("1000.00"),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
            **kw,
        )

    def poner(self, cuenta, posicion):
        with transaction.atomic():
            Cuenta.objects.filter(pk=cuenta.pk).update(posicion=posicion)
            connection.cursor().execute("SET CONSTRAINTS cuenta_posicion_unica IMMEDIATE")

    def test_dos_cuentas_con_la_misma_posicion_fallan(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cuenta.objects.filter(pk=self.a.pk).update(posicion=1)
            Cuenta.objects.filter(pk=self.b.pk).update(posicion=1)
            connection.cursor().execute("SET CONSTRAINTS cuenta_posicion_unica IMMEDIATE")

    def test_posiciones_distintas_se_guardan(self):
        self.poner(self.a, 1)
        self.poner(self.b, 2)

    def test_efectivo_con_posicion_falla(self):
        e = Cuenta.objects.create(destinatario=self.dest, tipo="efectivo")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cuenta.objects.filter(pk=e.pk).update(posicion=1)

    def test_cuenta_inactiva_con_posicion_falla(self):
        i = self.transf("alias.tres", estado="inactiva")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cuenta.objects.filter(pk=i.pk).update(posicion=1)

    def test_posicion_cero_falla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cuenta.objects.filter(pk=self.a.pk).update(posicion=0)


class SincronizarTest(SistemaTestCase):
    def setUp(self):
        self.dm = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo", tipo="profesional"
        )
        self.dj = crear_destinatario(usuario=None, nombre="Jimena", tipo="profesional")
        self.dr = crear_destinatario(usuario=None, nombre="Rodrigo", tipo="profesional")
        self.c1 = self.transf(self.dm, "alias.uno", "500000.00", "0.00", "10000.00")
        self.c2 = self.transf(self.dj, "jimena.roble.rana", "300000.00", "100000.00", "0.00")
        self.c3 = self.transf(self.dr, "rodrigo.mp", "200000.00", "0.00", "50000.00")

    def transf(self, dest, alias, mensual, semanal, minimo):
        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias=alias,
            tope_mensual=D(mensual),
            tope_semanal=D(semanal),
            minimo_por_pago=D(minimo),
        )

    def assert_sin_huecos(self):
        posiciones = list(
            Cuenta.objects.filter(posicion__isnull=False)
            .order_by("posicion")
            .values_list("posicion", flat=True)
        )
        self.assertEqual(posiciones, list(range(1, len(posiciones) + 1)))

    def posiciones(self):
        return [Cuenta.objects.get(pk=c.pk).posicion for c in (self.c1, self.c2, self.c3)]

    def test_los_3_datos_de_ejemplo_quedan_1_2_3(self):
        self.assertEqual(self.posiciones(), [1, 2, 3])
        self.assert_sin_huecos()

    def test_fila_fuera_de_atomic_falla(self):
        # SistemaTestCase envuelve todo en atomic; se simula la ausencia de transacción
        from unittest import mock

        with mock.patch.object(connection, "in_atomic_block", False):
            with self.assertRaises(RuntimeError):
                fila()

    def test_fila_dentro_de_atomic_no_falla(self):
        with transaction.atomic():
            fila()

    def test_sincronizar_dos_veces_no_graba_eventos(self):
        antes = Evento.objects.count()
        with transaction.atomic():
            sincronizar_lista(usuario=None)
            sincronizar_lista(usuario=None)
        self.assertEqual(Evento.objects.count(), antes)

    def test_efectivo_y_pos_quedan_sin_posicion(self):
        efectivo = crear_cuenta(usuario=None, tipo="efectivo", destinatario=self.dm)
        self.assertIsNone(Cuenta.objects.get(pk=efectivo.pk).posicion)
        self.assert_sin_huecos()

    def test_cuenta_de_destinatario_inactivo_queda_fuera(self):
        from sistema.destinatarios.servicios import cambiar_estado_destinatario

        cambiar_estado_destinatario(self.dj, "inactivo", usuario=None)
        self.assertEqual(self.posiciones(), [1, None, 2])
        self.assert_sin_huecos()
        cambiar_estado_destinatario(self.dj, "activo", usuario=None)
        self.assertEqual(self.posiciones(), [1, 3, 2])

    def test_cuenta_inactiva_sale_y_las_demas_corren(self):
        cambiar_estado_cuenta(self.c1, "inactiva", usuario=None)
        self.assertEqual(self.posiciones(), [None, 1, 2])
        self.assert_sin_huecos()

    def test_cuenta_que_vuelve_va_al_final(self):
        cambiar_estado_cuenta(self.c1, "inactiva", usuario=None)
        cambiar_estado_cuenta(self.c1, "activa", usuario=None)
        self.assertEqual(self.posiciones(), [3, 1, 2])

    def test_cambio_de_estado_graba_un_solo_evento_con_estado_y_posicion(self):
        antes = Evento.objects.filter(entidad="destinatarios.cuenta", entidad_id=self.c1.pk).count()
        cambiar_estado_cuenta(self.c1, "inactiva", usuario=None)
        eventos = Evento.objects.filter(entidad="destinatarios.cuenta", entidad_id=self.c1.pk)
        self.assertEqual(eventos.count(), antes + 1)
        campos = {c["campo"] for c in eventos.order_by("-pk").first().cambios}
        self.assertTrue({"estado", "posicion"} <= campos)

    def test_cuenta_corrida_tiene_su_evento(self):
        cambiar_estado_cuenta(self.c1, "inactiva", usuario=None)
        ultimo = Evento.objects.filter(entidad="destinatarios.cuenta", entidad_id=self.c2.pk)
        cambios = ultimo.order_by("-pk").first().cambios
        self.assertEqual(
            [(c["campo"], c["anterior"], c["nuevo"]) for c in cambios], [("posicion", 2, 1)]
        )

    def test_alta_de_tincho_tiene_un_evento_con_posicion_1(self):
        eventos = Evento.objects.filter(entidad="destinatarios.cuenta", entidad_id=self.c1.pk)
        self.assertEqual(eventos.count(), 1)
        posicion = [c for c in eventos.first().cambios if c["campo"] == "posicion"][0]
        self.assertEqual(posicion["nuevo"], 1)


class ListaEjemploMixin:
    def orden(self):
        return list(
            Cuenta.objects.filter(posicion__isnull=False)
            .order_by("posicion")
            .values_list("alias", flat=True)
        )

    def assert_sin_huecos(self):
        posiciones = list(
            Cuenta.objects.filter(posicion__isnull=False)
            .order_by("posicion")
            .values_list("posicion", flat=True)
        )
        self.assertEqual(posiciones, list(range(1, len(posiciones) + 1)))


class MoverTest(ListaEjemploMixin, DatosEjemplo):
    def test_us4_1_mover_rodrigo_a_1_graba_3_eventos_del_administrador(self):
        antes = Evento.objects.count()
        mover_cuenta(self.c3, 1, usuario=self.user)
        self.assertEqual(self.orden(), ["rodrigo.mp", "alias.uno", "jimena.roble.rana"])
        nuevos = Evento.objects.order_by("-pk")[: Evento.objects.count() - antes]
        self.assertEqual(len(nuevos), 3)
        self.assertEqual({e.usuario_id for e in nuevos}, {self.user.pk})
        self.assert_sin_huecos()

    def test_mover_de_1_a_3(self):
        mover_cuenta(self.c1, 3, usuario=self.user)
        self.assertEqual(self.orden(), ["jimena.roble.rana", "rodrigo.mp", "alias.uno"])
        self.assert_sin_huecos()

    def test_posicion_cero_falla(self):
        with self.assertRaisesMessage(ValidationError, "La posición debe ir de 1 a 3."):
            mover_cuenta(self.c1, 0, usuario=self.user)
        self.assert_sin_huecos()

    def test_posicion_n_mas_uno_falla(self):
        with self.assertRaisesMessage(ValidationError, "La posición debe ir de 1 a 3."):
            mover_cuenta(self.c1, 4, usuario=self.user)
        self.assertEqual(self.orden(), ["alias.uno", "jimena.roble.rana", "rodrigo.mp"])

    def test_ultima_posicion_n_es_valida(self):
        mover_cuenta(self.c2, 3, usuario=self.user)
        self.assertEqual(self.orden(), ["alias.uno", "rodrigo.mp", "jimena.roble.rana"])

    def test_misma_posicion_no_cambia_ni_graba_eventos(self):
        antes = Evento.objects.count()
        mover_cuenta(self.c2, 2, usuario=self.user)
        self.assertEqual(Evento.objects.count(), antes)
        self.assert_sin_huecos()

    def test_cuenta_inactiva_falla(self):
        cambiar_estado_cuenta(self.c1, "inactiva", usuario=None)
        with self.assertRaisesMessage(
            ValidationError, "La cuenta no está en la lista de prioridad."
        ):
            mover_cuenta(self.c1, 1, usuario=self.user)
        self.assert_sin_huecos()


class EstadosTest(ListaEjemploMixin, DatosEjemplo):
    def test_us4_2_inactiva_sale_y_graba_un_solo_evento_con_estado_y_posicion(self):
        antes = Evento.objects.filter(entidad="destinatarios.cuenta", entidad_id=self.c1.pk).count()
        cambiar_estado_cuenta(self.c1, "inactiva", usuario=None)
        self.assertEqual(self.orden(), ["jimena.roble.rana", "rodrigo.mp"])
        eventos = Evento.objects.filter(entidad="destinatarios.cuenta", entidad_id=self.c1.pk)
        self.assertEqual(eventos.count(), antes + 1)
        campos = {c["campo"] for c in eventos.order_by("-pk").first().cambios}
        self.assertTrue({"estado", "posicion"} <= campos)
        self.assert_sin_huecos()

    def test_archivada_sale_igual(self):
        cambiar_estado_cuenta(self.c1, "archivada", usuario=None)
        self.assertEqual(self.orden(), ["jimena.roble.rana", "rodrigo.mp"])
        self.assert_sin_huecos()

    def test_us4_3_reactivar_entra_en_la_posicion_3(self):
        cambiar_estado_cuenta(self.c1, "inactiva", usuario=None)
        cambiar_estado_cuenta(self.c1, "activa", usuario=None)
        self.assertEqual(Cuenta.objects.get(pk=self.c1.pk).posicion, 3)
        self.assert_sin_huecos()

    def test_us4_4_cuenta_nueva_entra_en_la_posicion_4(self):
        nueva = self.transf(self.dm, "martin.piedra.casa", "100000.00", "0.00", "0.00")
        self.assertEqual(Cuenta.objects.get(pk=nueva.pk).posicion, 4)
        self.assert_sin_huecos()

    def test_cuenta_nueva_inactiva_no_tiene_posicion(self):
        nueva = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dm,
            alias="martin.inactiva",
            tope_mensual=D("100.00"),
            estado="inactiva",
        )
        self.assertIsNone(Cuenta.objects.get(pk=nueva.pk).posicion)
        self.assert_sin_huecos()

    def test_us4_5_destinatario_inactivo_saca_sus_cuentas(self):
        cambiar_estado_destinatario(self.dj, "inactivo", usuario=None)
        self.assertEqual(self.orden(), ["alias.uno", "rodrigo.mp"])
        self.assert_sin_huecos()

    def test_cambiar_destinatario_de_la_cuenta_conserva_la_posicion(self):
        editar_cuenta(self.c3, usuario=None, destinatario=self.dm)
        self.assertEqual(Cuenta.objects.get(pk=self.c3.pk).posicion, 3)
        self.assert_sin_huecos()

    def test_destinatario_con_2_cuentas_vuelve_al_final_en_orden_de_alta(self):
        self.transf(self.dr, "rodrigo.dos", "100000.00", "0.00", "0.00")
        cambiar_estado_destinatario(self.dr, "inactivo", usuario=None)
        self.assertEqual(self.orden(), ["alias.uno", "jimena.roble.rana"])
        cambiar_estado_destinatario(self.dr, "activo", usuario=None)
        self.assertEqual(
            self.orden(), ["alias.uno", "jimena.roble.rana", "rodrigo.mp", "rodrigo.dos"]
        )
        self.assert_sin_huecos()
