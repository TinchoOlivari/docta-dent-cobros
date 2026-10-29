from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import historial_de
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta, Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.models import Sesion
from sistema.terminales.servicios import (
    abrir_sesion,
    cambiar_cuenta,
    cerrar_sesion,
    crear_terminal,
)

AHORA = datetime(2026, 10, 14, 10, 0, tzinfo=ARG)


def h(hora, minuto=0, segundo=0):
    return datetime(2026, 10, 14, hora, minuto, segundo, tzinfo=ARG)


class SesionBase(SistemaTestCase):
    ahora = AHORA

    def setUp(self):
        self.mp = Proveedor.objects.get(nombre="Mercadopago")
        self.payway = Proveedor.objects.get(nombre="Payway")
        self.rodrigo = Destinatario.objects.create(nombre="Rodrigo", tipo="clinica")
        self.jimena = Destinatario.objects.create(nombre="Jimena", tipo="clinica")
        self.cuenta_a = self.pos(self.rodrigo)
        self.cuenta_b = self.pos(self.jimena)
        self.terminal = crear_terminal(
            usuario=None, nombre="Point Smart #1", proveedor=self.mp, acepta_debito=True
        )
        patcher = mock.patch("django.utils.timezone.now", return_value=self.ahora)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pos(self, destinatario, proveedor=None):
        return crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=destinatario,
            titular="Titular Test",
            proveedor=proveedor or self.mp,
            objetivo=Decimal("300000.00"),
        )

    def abrir(self, cuenta=None, inicio=None, terminal=None):
        return abrir_sesion(
            terminal or self.terminal, cuenta or self.cuenta_a, inicio or h(9), usuario=None
        )

    def eventos(self, obj, tipo):
        return historial_de(obj).filter(tipo=tipo).count()


class AbrirSesionTest(SesionBase):
    def test_us2_1_abre_sin_fin_con_evento_alta(self):
        s = self.abrir()
        self.assertIsNone(s.fin)
        self.assertEqual(s.inicio, h(9))
        self.assertEqual(self.eventos(s, Evento.Tipo.ALTA), 1)

    def test_inicio_pasado_guarda(self):
        self.assertEqual(self.abrir(inicio=h(9)).inicio, h(9))

    def test_inicio_justo_ahora_guarda_y_un_segundo_despues_se_rechaza(self):
        self.abrir(inicio=h(10))
        otra = crear_terminal(
            usuario=None, nombre="Point Mini #1", proveedor=self.mp, acepta_debito=True
        )
        with self.assertRaisesMessage(ValidationError, "El inicio no puede estar en el futuro."):
            self.abrir(inicio=h(10, 0, 1), terminal=otra)
        with self.assertRaisesMessage(ValidationError, "El inicio no puede estar en el futuro."):
            self.abrir(inicio=h(10, 1), terminal=otra)

    def test_cuenta_pasada_a_destinatario_inactivo_tras_leerla_se_rechaza(self):
        inactivo = Destinatario.objects.create(
            nombre="Inactivo", tipo="clinica", estado=Destinatario.Estado.INACTIVO
        )
        # self.cuenta_a en memoria sigue con el destinatario viejo (Activo)
        Cuenta.objects.filter(pk=self.cuenta_a.pk).update(destinatario=inactivo)
        with self.assertRaisesMessage(
            ValidationError, "El destinatario de la cuenta no está Activo."
        ):
            self.abrir()
        self.assertFalse(Sesion.objects.exists())

    def test_us2_6_proveedor_distinto_se_rechaza(self):
        payway = self.pos(self.rodrigo, self.payway)
        with self.assertRaisesMessage(
            ValidationError, "El proveedor de la cuenta no es el de la terminal."
        ):
            self.abrir(cuenta=payway)

    def test_cuenta_de_transferencia_se_rechaza(self):
        transf = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.rodrigo,
            titular="Titular Test",
            alias="alias.test.uno",
            tope_mensual=Decimal("1000.00"),
        )
        with self.assertRaisesMessage(ValidationError, "La cuenta debe ser de terminal POS."):
            self.abrir(cuenta=transf)

    def test_us2_7_misma_cuenta_en_dos_terminales(self):
        otra = crear_terminal(
            usuario=None, nombre="Point Mini #1", proveedor=self.mp, acepta_debito=True
        )
        self.abrir()
        self.abrir(terminal=otra)
        self.assertEqual(Sesion.objects.filter(cuenta=self.cuenta_a, fin=None).count(), 2)

    def test_us2_8_superposicion_con_sesion_cerrada(self):
        s = self.abrir(inicio=h(9))
        cerrar_sesion(s, h(10), usuario=None)
        # cerrada 9-10: inicio 9:59:59 se superpone; 10:00 (justo el fin) no
        with self.assertRaisesMessage(
            ValidationError, "La sesión se superpone con otra sesión de la terminal."
        ):
            self.abrir(cuenta=self.cuenta_b, inicio=h(9, 59, 59))
        self.abrir(cuenta=self.cuenta_b, inicio=h(10))

    def test_segunda_sesion_abierta_en_la_misma_terminal_se_rechaza(self):
        self.abrir()
        with self.assertRaisesMessage(
            ValidationError, "La sesión se superpone con otra sesión de la terminal."
        ):
            self.abrir(cuenta=self.cuenta_b, inicio=h(9, 30))

    def test_fr9_terminal_inactiva_se_rechaza(self):
        self.terminal.estado = "inactiva"
        self.terminal.save()
        with self.assertRaisesMessage(ValidationError, "La terminal no está Activa."):
            self.abrir()

    def test_fr9_cuenta_inactiva_se_rechaza(self):
        Cuenta.objects.filter(pk=self.cuenta_a.pk).update(estado="inactiva", posicion=None)
        with self.assertRaisesMessage(ValidationError, "La cuenta no está Activa."):
            self.abrir()

    def test_fr9_destinatario_inactivo_se_rechaza(self):
        Destinatario.objects.filter(pk=self.rodrigo.pk).update(estado="inactivo")
        with self.assertRaisesMessage(
            ValidationError, "El destinatario de la cuenta no está Activo."
        ):
            self.abrir()

    def test_fr9_valida_el_estado_de_la_base_no_el_del_objeto_recibido(self):
        Cuenta.objects.filter(pk=self.cuenta_a.pk).update(estado="inactiva", posicion=None)
        self.assertEqual(self.cuenta_a.estado, "activa")  # objeto desactualizado
        with self.assertRaisesMessage(ValidationError, "La cuenta no está Activa."):
            self.abrir()


class CambiarCuentaTest(SesionBase):
    ahora = h(12)

    def test_us2_2_cambio_deja_sesiones_seguidas(self):
        a = self.abrir(inicio=h(9))
        b = cambiar_cuenta(self.terminal, self.cuenta_b, h(12), usuario=None)
        a.refresh_from_db()
        self.assertEqual(a.fin, b.inicio)
        self.assertEqual(b.inicio, h(12))
        self.assertIsNone(b.fin)
        self.assertEqual(self.eventos(a, Evento.Tipo.CAMBIO), 1)
        self.assertEqual(self.eventos(b, Evento.Tipo.ALTA), 1)

    def test_cambio_igual_al_inicio_se_rechaza_y_un_segundo_despues_pasa(self):
        a = self.abrir(inicio=h(9))
        msg = "El cambio debe ser posterior al inicio de la sesión actual."
        with self.assertRaisesMessage(ValidationError, msg):
            cambiar_cuenta(self.terminal, self.cuenta_b, h(9), usuario=None)
        with self.assertRaisesMessage(ValidationError, msg):
            cambiar_cuenta(self.terminal, self.cuenta_b, h(8, 59, 59), usuario=None)
        cambiar_cuenta(self.terminal, self.cuenta_b, h(9, 0, 1), usuario=None)
        a.refresh_from_db()
        self.assertEqual(a.fin, h(9, 0, 1))

    def test_cambio_en_el_futuro_se_rechaza(self):
        self.abrir(inicio=h(9))
        with self.assertRaisesMessage(ValidationError, "El cambio no puede estar en el futuro."):
            cambiar_cuenta(self.terminal, self.cuenta_b, h(12, 0, 1), usuario=None)

    def test_sin_sesion_abierta_se_rechaza(self):
        with self.assertRaisesMessage(ValidationError, "La terminal no tiene una sesión abierta."):
            cambiar_cuenta(self.terminal, self.cuenta_b, h(11), usuario=None)

    def test_cuenta_nueva_inactiva_se_rechaza_y_la_actual_sigue_abierta(self):
        a = self.abrir(inicio=h(9))
        Cuenta.objects.filter(pk=self.cuenta_b.pk).update(estado="inactiva", posicion=None)
        with self.assertRaisesMessage(ValidationError, "La cuenta no está Activa."):
            cambiar_cuenta(self.terminal, self.cuenta_b, h(11), usuario=None)
        a.refresh_from_db()
        self.assertIsNone(a.fin)

    def test_cambio_retroactivo_reasigna_solo_los_cobros_desde_la_hora(self):
        self.abrir(inicio=h(9))

        def cobro(fecha):
            return cobro_prueba(
                cuenta=self.cuenta_a,
                terminal=self.terminal,
                destinatario=self.rodrigo,
                monto=Decimal("100.00"),
                estado="finalizado",
                metodo="debito",
                fecha=fecha,
            )

        antes, justo, despues = cobro(h(10, 30)), cobro(h(11)), cobro(h(11, 30))
        cambiar_cuenta(self.terminal, self.cuenta_b, h(11), usuario=None)
        for c in (antes, justo, despues):
            c.refresh_from_db()
        self.assertEqual(antes.cuenta, self.cuenta_a)
        self.assertEqual(justo.cuenta, self.cuenta_b)  # el cambio es inclusivo
        self.assertEqual(despues.cuenta, self.cuenta_b)
        self.assertEqual(despues.destinatario, self.jimena)
        evento = historial_de(despues).filter(tipo=Evento.Tipo.CAMBIO).first()
        self.assertEqual(evento.origen, "corrección de sesión")
        self.assertEqual(self.eventos(antes, Evento.Tipo.CAMBIO), 0)


class CerrarSesionTest(SesionBase):
    ahora = h(20)

    def test_us2_3_cierre_deja_la_terminal_sin_sesion_abierta(self):
        s = self.abrir(inicio=h(9))
        cerrar_sesion(s, h(20), usuario=None)
        s.refresh_from_db()
        self.assertEqual(s.fin, h(20))
        self.assertFalse(self.terminal.sesiones.filter(fin__isnull=True).exists())
        self.assertEqual(self.eventos(s, Evento.Tipo.CAMBIO), 1)

    def test_fin_en_el_futuro_se_rechaza(self):
        s = self.abrir(inicio=h(9))
        with self.assertRaisesMessage(ValidationError, "El fin no puede estar en el futuro."):
            cerrar_sesion(s, h(20, 0, 1), usuario=None)

    def test_fin_igual_al_inicio_se_rechaza(self):
        s = self.abrir(inicio=h(9))
        with self.assertRaisesMessage(ValidationError, "El inicio debe ser anterior al fin."):
            cerrar_sesion(s, h(9), usuario=None)

    def test_sesion_ya_cerrada_se_rechaza(self):
        s = self.abrir(inicio=h(9))
        cerrar_sesion(s, h(12), usuario=None)
        with self.assertRaisesMessage(ValidationError, "La sesión ya está cerrada."):
            cerrar_sesion(s, h(13), usuario=None)

    def test_cobro_posterior_al_cierre_rechaza_y_la_sesion_sigue_abierta(self):
        s = self.abrir(inicio=h(9))
        c = cobro_prueba(
            cuenta=self.cuenta_a, terminal=self.terminal, destinatario=self.rodrigo,
            monto=Decimal("100.00"), estado="finalizado", metodo="debito", fecha=h(19),
        )  # fmt: skip
        with self.assertRaisesMessage(ValidationError, "queda sin sesión"):
            cerrar_sesion(s, h(18), usuario=None)
        s.refresh_from_db()
        self.assertIsNone(s.fin)
        # cobro justo en el fin: queda fuera de la sesión (fin exclusivo)
        c.fecha = h(18)
        c.save()
        with self.assertRaisesMessage(ValidationError, "queda sin sesión"):
            cerrar_sesion(s, h(18), usuario=None)
        c.fecha = h(17, 59, 59)
        c.save()
        cerrar_sesion(s, h(18), usuario=None)
