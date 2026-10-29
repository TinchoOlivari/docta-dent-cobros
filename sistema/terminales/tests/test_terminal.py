from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db.models import ProtectedError

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.auditoria.servicios import historial_de, lineas
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Destinatario, Proveedor
from sistema.destinatarios.servicios import crear_cuenta
from sistema.terminales.models import Sesion, Terminal
from sistema.terminales.servicios import (
    cambiar_estado_terminal,
    crear_terminal,
    editar_terminal,
)

AHORA = datetime(2026, 10, 14, 10, 0, tzinfo=ARG)


class TerminalServiciosTest(SistemaTestCase):
    def setUp(self):
        self.mp = Proveedor.objects.get(nombre="Mercadopago")
        self.payway = Proveedor.objects.get(nombre="Payway")

    def nueva(self, nombre="Point Smart #1", **kw):
        kw.setdefault("acepta_debito", True)
        return crear_terminal(usuario=None, nombre=nombre, proveedor=self.mp, **kw)

    def sesion_abierta(self, terminal):
        d = Destinatario.objects.create(nombre="Rodrigo", tipo="clinica")
        cuenta = crear_cuenta(
            usuario=None,
            tipo="terminal_pos",
            destinatario=d,
            titular="Titular Test",
            proveedor=self.mp,
            objetivo=Decimal("300000.00"),
        )
        return Sesion.objects.create(terminal=terminal, cuenta=cuenta, inicio=AHORA)

    def test_us1_1_alta_activa_con_tres_metodos_y_un_evento(self):
        with mock.patch("django.utils.timezone.now", return_value=AHORA):
            t = self.nueva(acepta_credito=True, acepta_qr=True)
        self.assertEqual(t.estado, "activa")
        self.assertEqual(t.fecha_activacion, AHORA)
        self.assertTrue(t.acepta("debito") and t.acepta("credito") and t.acepta("qr"))
        self.assertFalse(t.acepta("link"))
        self.assertEqual(historial_de(t).count(), 1)

    def test_us1_2_nombre_repetido_sin_distinguir_mayusculas(self):
        self.nueva("point smart #1")
        with self.assertRaisesMessage(ValidationError, "Ya existe una terminal con ese nombre."):
            self.nueva("POINT SMART #1")

    def test_us1_3_sin_metodos_se_rechaza(self):
        with self.assertRaisesMessage(ValidationError, "Elegí al menos un método de cobro."):
            self.nueva(acepta_debito=False)

    def test_desactivar_qr_deja_debito_y_link_y_registra_el_cambio(self):
        t = self.nueva(acepta_qr=True, acepta_link=True)
        editar_terminal(t, usuario=None, acepta_qr=False)
        t.refresh_from_db()
        self.assertTrue(t.acepta("debito") and t.acepta("link"))
        self.assertIs(t.acepta("qr"), False)
        evento = historial_de(t).first()
        self.assertIn(("Acepta QR", "Sí", "No"), lineas(evento))

    def test_editar_sin_cambios_no_registra_evento(self):
        t = self.nueva()
        editar_terminal(t, usuario=None, nombre=t.nombre)
        self.assertEqual(historial_de(t).count(), 1)

    def test_editar_sin_dejar_metodos_se_rechaza(self):
        t = self.nueva()
        with self.assertRaisesMessage(ValidationError, "Elegí al menos un método de cobro."):
            editar_terminal(t, usuario=None, acepta_debito=False)

    def test_us1_4_desactivar_credito_no_toca_un_cobro_anterior(self):
        t = self.nueva(acepta_credito=True)
        d = Destinatario.objects.create(nombre="Rodrigo", tipo="clinica")
        cuenta = crear_cuenta(
            usuario=None, tipo="terminal_pos", destinatario=d, titular="Titular Test",
            proveedor=self.mp, objetivo=Decimal("300000.00"),
        )  # fmt: skip
        cobro = cobro_prueba(
            cuenta=cuenta, terminal=t, destinatario=d, monto=Decimal("100.00"),
            estado="finalizado", metodo="credito", fecha=AHORA,
        )  # fmt: skip
        editar_terminal(t, usuario=None, acepta_credito=False)
        cobro.refresh_from_db()
        self.assertEqual(cobro.metodo, "credito")
        self.assertEqual(cobro.terminal_id, t.pk)

    def test_us1_5_el_proveedor_no_cambia(self):
        t = self.nueva()
        with self.assertRaises(ValidationError):
            editar_terminal(t, usuario=None, proveedor=self.payway)
        t.proveedor = self.payway
        with self.assertRaisesMessage(ValidationError, "El proveedor no cambia después del alta."):
            t.full_clean()

    def test_estado_no_se_cambia_con_editar(self):
        t = self.nueva()
        with self.assertRaises(ValidationError):
            editar_terminal(t, usuario=None, estado="inactiva")

    def test_fr3_cualquier_estado_a_cualquier_estado_con_fechas(self):
        t = self.nueva()
        with mock.patch("django.utils.timezone.now", return_value=AHORA):
            cambiar_estado_terminal(t, "inactiva", usuario=None)
            cambiar_estado_terminal(t, "archivada", usuario=None)
        t.refresh_from_db()
        self.assertEqual((t.estado, t.fecha_archivado), ("archivada", AHORA))
        with mock.patch("django.utils.timezone.now", return_value=AHORA):
            cambiar_estado_terminal(t, "activa", usuario=None)
        t.refresh_from_db()
        self.assertEqual((t.estado, t.fecha_activacion), ("activa", AHORA))
        self.assertEqual(historial_de(t).count(), 4)

    def test_mismo_estado_no_registra_evento(self):
        t = self.nueva()
        cambiar_estado_terminal(t, "activa", usuario=None)
        self.assertEqual(historial_de(t).count(), 1)

    def test_fr4_inactiva_o_archivada_con_sesion_abierta_se_rechaza(self):
        t = self.nueva()
        sesion = self.sesion_abierta(t)
        for estado in ("inactiva", "archivada"):
            with self.assertRaisesMessage(
                ValidationError, "La terminal tiene una sesión abierta. Cerrá la sesión primero."
            ):
                cambiar_estado_terminal(t, estado, usuario=None)
            t.refresh_from_db()
            self.assertEqual(t.estado, "activa")
        # una sesión cerrada no frena
        Sesion.objects.filter(pk=sesion.pk).update(fin=AHORA.replace(hour=11))
        cambiar_estado_terminal(t, "inactiva", usuario=None)
        t.refresh_from_db()
        self.assertEqual(t.estado, "inactiva")

    def test_fr5_la_terminal_no_se_borra(self):
        t = self.nueva()
        with self.assertRaises(ProtectedError):
            t.delete()
        with self.assertRaises(ProtectedError):
            Terminal.objects.all().delete()
