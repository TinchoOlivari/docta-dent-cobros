from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import historial_de, quien
from sistema.cobros.models import Cobro, ItemCobro
from sistema.destinatarios.models import Cuenta
from sistema.terminales.models import Sesion, Terminal
from sistema.terminales.servicios import (
    _reasignar,
    borrar_sesion,
    cambiar_cuenta,
    cerrar_sesion,
    corregir_cambio,
    editar_sesion,
)
from sistema.terminales.tests.test_sesion import SesionBase, h


class CorreccionBase(SesionBase):
    """A (Rodrigo MP) 9-12 y B (Jimena MP) desde 12. Ahora son las 14:00."""

    ahora = h(14)

    def setUp(self):
        super().setUp()
        self.a = self.abrir(inicio=h(9))
        self.b = cambiar_cuenta(self.terminal, self.cuenta_b, h(12), usuario=None)
        self.a.refresh_from_db()

    def cobro(self, fecha, cuenta=None, estado="finalizado", terminal=None, monto="100.00"):
        cuenta = cuenta or (self.cuenta_a if fecha < h(12) else self.cuenta_b)
        return cobro_prueba(
            cuenta=cuenta,
            terminal=terminal or self.terminal,
            destinatario=cuenta.destinatario,
            monto=Decimal(monto),
            estado=estado,
            metodo="debito",
            fecha=fecha,
        )

    def estado(self):
        return (
            list(Sesion.objects.order_by("pk").values_list("inicio", "fin", "cuenta")),
            list(Cobro.objects.order_by("pk").values_list("cuenta", "destinatario")),
            Evento.objects.count(),
        )


class CorregirCambioTest(CorreccionBase):
    def test_us4_1_hora_anterior_reasigna_desde_la_hora_nueva(self):
        c1030, c1100, c1145 = self.cobro(h(10, 30)), self.cobro(h(11)), self.cobro(h(11, 45))
        n = corregir_cambio(self.a, h(11), usuario=None)
        for c in (c1030, c1100, c1145):
            c.refresh_from_db()
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(n, 2)
        self.assertEqual(c1030.cuenta, self.cuenta_a)
        self.assertEqual((c1100.cuenta, c1100.destinatario), (self.cuenta_b, self.jimena))
        self.assertEqual((c1145.cuenta, c1145.destinatario), (self.cuenta_b, self.jimena))
        self.assertEqual((self.a.fin, self.b.inicio), (h(11), h(11)))
        self.assertEqual(self.eventos(c1030, Evento.Tipo.CAMBIO), 0)

    def test_us4_2_hora_posterior_devuelve_cobros_a_la_primera(self):
        c = self.cobro(h(12, 30))
        corregir_cambio(self.a, h(13), usuario=None)
        c.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(c.cuenta, self.cuenta_a)
        self.assertEqual(c.destinatario, self.rodrigo)
        self.assertEqual(self.b.inicio, h(13))

    def test_cobro_justo_en_la_hora_nueva_pasa_a_la_segunda_y_un_segundo_antes_no(self):
        justo, antes = self.cobro(h(11)), self.cobro(h(10, 59, 59))
        corregir_cambio(self.a, h(11), usuario=None)
        justo.refresh_from_db()
        antes.refresh_from_db()
        self.assertEqual(justo.cuenta, self.cuenta_b)
        self.assertEqual(antes.cuenta, self.cuenta_a)

    def test_us4_3_hora_antes_del_inicio_de_a_se_rechaza_y_nada_cambia(self):
        self.cobro(h(10))
        previo = self.estado()
        for hora in (h(9), h(8)):
            with self.assertRaisesMessage(ValidationError, "El inicio debe ser anterior al fin."):
                corregir_cambio(self.a, hora, usuario=None)
        self.assertEqual(self.estado(), previo)
        corregir_cambio(self.a, h(9, 0, 1), usuario=None)  # un segundo arriba del límite

    def test_hora_en_el_futuro_se_rechaza_y_justo_ahora_pasa(self):
        with self.assertRaisesMessage(ValidationError, "El cambio no puede estar en el futuro."):
            corregir_cambio(self.a, h(14, 0, 1), usuario=None)
        # B no tiene fin: la hora nueva puede ser posterior al cambio hasta ahora
        corregir_cambio(self.a, h(14), usuario=None)

    def test_hora_posterior_al_fin_de_b_se_rechaza(self):
        cerrar_sesion(self.b, h(13), usuario=None)
        with self.assertRaisesMessage(ValidationError, "El inicio debe ser anterior al fin."):
            corregir_cambio(self.a, h(13), usuario=None)
        corregir_cambio(self.a, h(12, 59, 59), usuario=None)

    def test_misma_hora_no_hace_nada(self):
        previo = self.estado()
        self.assertEqual(corregir_cambio(self.a, h(12), usuario=None), 0)
        self.assertEqual(self.estado(), previo)

    def test_a_con_hueco_hasta_b_se_rechaza(self):
        editar_sesion(self.a, usuario=None, fin=h(11))
        msg = "La sesión no tiene una sesión seguida."
        with self.assertRaisesMessage(ValidationError, msg):
            corregir_cambio(self.a, h(10), usuario=None)

    def test_a_abierta_se_rechaza(self):
        with self.assertRaisesMessage(ValidationError, "La sesión no tiene una sesión seguida."):
            corregir_cambio(self.b, h(13), usuario=None)

    def test_b_de_otra_terminal_no_cuenta_como_seguida(self):
        otra = Terminal.objects.create(nombre="Otra", proveedor=self.mp, acepta_debito=True)
        editar_sesion(self.a, usuario=None, fin=h(11))
        self.abrir(inicio=h(11), terminal=otra)
        with self.assertRaisesMessage(ValidationError, "La sesión no tiene una sesión seguida."):
            corregir_cambio(self.a, h(10), usuario=None)

    def test_cobro_cancelado_tambien_se_reasigna(self):
        c = self.cobro(h(11, 30), cuenta=self.cuenta_a, estado="cancelado")
        corregir_cambio(self.a, h(11), usuario=None)
        c.refresh_from_db()
        self.assertEqual(c.cuenta, self.cuenta_b)
        self.assertEqual(c.estado, "cancelado")

    def test_solo_cambian_los_cobros_que_cambian_de_cuenta(self):
        ya_en_b = self.cobro(h(12, 30))
        otra = Terminal.objects.create(nombre="Otra", proveedor=self.mp, acepta_debito=True)
        ajeno = self.cobro(h(11, 30), cuenta=self.cuenta_a, terminal=otra)
        cambia = self.cobro(h(11, 30))
        self.assertEqual(corregir_cambio(self.a, h(11), usuario=None), 1)
        self.assertEqual(self.eventos(ya_en_b, Evento.Tipo.CAMBIO), 0)
        self.assertEqual(self.eventos(ajeno, Evento.Tipo.CAMBIO), 0)
        self.assertEqual(self.eventos(cambia, Evento.Tipo.CAMBIO), 1)


class AtomicidadTest(CorreccionBase):
    def test_us4_4_falla_en_dentalink_deshace_todo(self):
        self.cobro(h(11, 30))
        previo = self.estado()
        with mock.patch("sistema.terminales.servicios.al_cambiar_cobro", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                corregir_cambio(self.a, h(11), usuario=None)
        self.assertEqual(self.estado(), previo)

    def test_us4_5_historial_de_sesiones_y_cobros(self):
        c1, c2 = self.cobro(h(11)), self.cobro(h(11, 30))
        corregir_cambio(self.a, h(11), usuario=None)
        # A: dos eventos de cambio (el de cierre y el de la corrección); B: igual
        self.assertEqual(self.eventos(self.a, Evento.Tipo.CAMBIO), 2)
        self.assertEqual(self.eventos(self.b, Evento.Tipo.CAMBIO), 1)
        ult_a = historial_de(self.a).filter(tipo=Evento.Tipo.CAMBIO).latest("pk")
        ult_b = historial_de(self.b).filter(tipo=Evento.Tipo.CAMBIO).latest("pk")
        self.assertEqual({x["campo"] for x in ult_a.cambios}, {"fin"})
        self.assertEqual({x["campo"] for x in ult_b.cambios}, {"inicio"})
        for c in (c1, c2):
            ev = historial_de(c).get(tipo=Evento.Tipo.CAMBIO)
            self.assertEqual(quien(ev), "Sistema — corrección de sesión")

    def test_edge_dentalink_item_enviado_pasa_a_requiere_ajuste(self):
        c = self.cobro(h(11, 30))
        item = ItemCobro.objects.create(
            cobro=c, numero=1, id_prestacion=9001, monto=c.monto, estado_envio="enviado"
        )
        corregir_cambio(self.a, h(11), usuario=None)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "requiere_ajuste")


class ReasignarTest(CorreccionBase):
    def test_sin_cobros_devuelve_cero(self):
        self.assertEqual(_reasignar(self.terminal, h(9), None), 0)

    def test_cobro_sin_sesion_rechaza_con_su_fecha(self):
        c = self.cobro(h(8, 59, 59), cuenta=self.cuenta_a)
        with self.assertRaisesMessage(ValidationError, f"El cobro {c.pk} del 14/10/2026 08:59:59"):
            _reasignar(self.terminal, h(8), None)

    def test_rango_desde_inclusivo_y_hasta_exclusivo(self):
        c1 = self.cobro(h(10), cuenta=self.cuenta_b)  # mal asignado
        c2 = self.cobro(h(11), cuenta=self.cuenta_b)  # mal asignado
        c3 = self.cobro(h(11, 30), cuenta=self.cuenta_b)  # mal asignado
        self.assertEqual(_reasignar(self.terminal, h(10), h(11)), 1)
        for c in (c1, c2, c3):
            c.refresh_from_db()
        self.assertEqual(c1.cuenta, self.cuenta_a)
        self.assertEqual(c2.cuenta, self.cuenta_b)  # hasta es exclusivo
        self.assertEqual(c3.cuenta, self.cuenta_b)
        self.assertEqual(_reasignar(self.terminal, h(10, 0, 1), h(11)), 0)  # desde inclusivo
        # c2 (11:00) queda fuera de [11:00:01, 11:30); c3 (11:30) entra por el desde inclusivo
        self.assertEqual(_reasignar(self.terminal, h(11, 0, 1), h(11, 30)), 0)
        self.assertEqual(_reasignar(self.terminal, h(11, 30), None), 1)
        self.assertEqual(_reasignar(self.terminal, h(11), h(11, 0, 1)), 1)

    def test_hasta_none_incluye_todo_lo_posterior(self):
        c = self.cobro(h(13), cuenta=self.cuenta_a)
        self.assertEqual(_reasignar(self.terminal, h(12), None), 1)
        c.refresh_from_db()
        self.assertEqual((c.cuenta, c.destinatario), (self.cuenta_b, self.jimena))

    def test_cobros_de_otra_terminal_no_se_tocan(self):
        otra = Terminal.objects.create(nombre="Otra", proveedor=self.mp, acepta_debito=True)
        c = self.cobro(h(13), cuenta=self.cuenta_a, terminal=otra)
        self.assertEqual(_reasignar(self.terminal, h(9), None), 0)
        c.refresh_from_db()
        self.assertEqual(c.cuenta, self.cuenta_a)

    def test_cobro_ya_en_su_cuenta_no_genera_evento(self):
        c = self.cobro(h(10))
        self.assertEqual(_reasignar(self.terminal, h(9), None), 0)
        self.assertEqual(self.eventos(c, Evento.Tipo.CAMBIO), 0)

    def test_evento_con_origen_y_sin_usuario(self):
        c = self.cobro(h(10), cuenta=self.cuenta_b)
        _reasignar(self.terminal, h(9), None)
        ev = historial_de(c).get(tipo=Evento.Tipo.CAMBIO)
        self.assertIsNone(ev.usuario_id)
        self.assertEqual(ev.origen, "corrección de sesión")

    def test_llama_a_dentalink_una_vez_por_cobro_reasignado(self):
        self.cobro(h(10), cuenta=self.cuenta_b)
        self.cobro(h(11), cuenta=self.cuenta_b)
        self.cobro(h(13))
        with mock.patch("sistema.terminales.servicios.al_cambiar_cobro") as al:
            _reasignar(self.terminal, h(9), None)
        self.assertEqual(al.call_count, 2)
        self.assertEqual(al.call_args.kwargs, {"usuario": None, "cancelado": False})

    def test_aviso_una_vez_por_cuenta_que_recibe_cobros(self):
        self.cobro(h(10), cuenta=self.cuenta_b)
        self.cobro(h(11), cuenta=self.cuenta_b)
        with mock.patch("sistema.terminales.servicios.avisar_objetivo") as av:
            _reasignar(self.terminal, h(9), None)
        av.assert_called_once()
        self.assertEqual(av.call_args.args[0], self.cuenta_a)
        self.assertEqual(av.call_args.args[1], Decimal("0.00"))


class EditarSesionTest(CorreccionBase):
    def test_campo_ajeno_se_rechaza(self):
        with self.assertRaisesMessage(ValidationError, "El campo terminal no se cambia"):
            editar_sesion(self.a, usuario=None, terminal=self.terminal)

    def test_us4_6_acortar_solo_a_deja_cobro_sin_sesion(self):
        self.cobro(h(11, 30))
        previo = self.estado()
        with self.assertRaisesMessage(ValidationError, "queda sin sesión"):
            editar_sesion(self.a, usuario=None, fin=h(11))
        self.assertEqual(self.estado(), previo)

    def test_acortar_a_justo_antes_del_cobro_rechaza_y_justo_despues_pasa(self):
        self.cobro(h(11, 30))
        with self.assertRaisesMessage(ValidationError, "queda sin sesión"):
            editar_sesion(self.a, usuario=None, fin=h(11, 30))  # fin exclusivo
        editar_sesion(self.a, usuario=None, fin=h(11, 30, 1))

    def test_cuenta_equivocada_en_sesion_cerrada_mueve_todos_los_cobros(self):
        inactiva = self.pos(self.jimena)
        Cuenta.objects.filter(pk=inactiva.pk).update(estado="inactiva", posicion=None)
        inactiva.refresh_from_db()
        c1, c2 = self.cobro(h(9)), self.cobro(h(11, 59, 59))
        sin_cambio = self.cobro(h(12))
        editar_sesion(self.a, usuario=None, cuenta=inactiva)
        for c in (c1, c2, sin_cambio):
            c.refresh_from_db()
        self.assertEqual((c1.cuenta, c1.destinatario), (inactiva, self.jimena))
        self.assertEqual(c2.cuenta, inactiva)
        self.assertEqual(sin_cambio.cuenta, self.cuenta_b)
        self.assertEqual(self.eventos(sin_cambio, Evento.Tipo.CAMBIO), 0)

    def test_sesion_cerrada_con_cuenta_de_otro_proveedor_se_rechaza(self):
        payway = self.pos(self.rodrigo, self.payway)
        with self.assertRaisesMessage(
            ValidationError, "El proveedor de la cuenta no es el de la terminal."
        ):
            editar_sesion(self.a, usuario=None, cuenta=payway)

    def test_sesion_abierta_con_cuenta_inactiva_se_rechaza(self):
        inactiva = self.pos(self.rodrigo)
        Cuenta.objects.filter(pk=inactiva.pk).update(estado="inactiva", posicion=None)
        inactiva.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "La cuenta no está Activa."):
            editar_sesion(self.b, usuario=None, cuenta=inactiva)

    def _cerrar_b_y(self, cuenta=None, destinatario=None, terminal=None):
        cerrar_sesion(self.b, h(13), usuario=None)
        self.b.refresh_from_db()
        if terminal:
            Terminal.objects.filter(pk=self.terminal.pk).update(estado=terminal)
        if cuenta:
            Cuenta.objects.filter(pk=self.cuenta_b.pk).update(estado=cuenta, posicion=None)
        if destinatario:
            self.jimena.__class__.objects.filter(pk=self.jimena.pk).update(estado=destinatario)
        self.terminal.refresh_from_db()
        return self.estado()

    def test_reabrir_con_cuenta_inactiva_se_rechaza_y_sigue_cerrada(self):
        previo = self._cerrar_b_y(cuenta="inactiva")
        with self.assertRaisesMessage(ValidationError, "La cuenta no está Activa."):
            editar_sesion(self.b, usuario=None, fin=None)
        self.assertEqual(self.estado(), previo)

    def test_reabrir_con_destinatario_inactivo_se_rechaza(self):
        previo = self._cerrar_b_y(destinatario="inactivo")
        with self.assertRaisesMessage(ValidationError, "El destinatario de la cuenta no está"):
            editar_sesion(self.b, usuario=None, fin=None)
        self.assertEqual(self.estado(), previo)

    def test_reabrir_con_terminal_inactiva_se_rechaza(self):
        previo = self._cerrar_b_y(terminal="inactiva")
        with self.assertRaisesMessage(ValidationError, "La terminal no está Activa."):
            editar_sesion(self.b, usuario=None, fin=None)
        self.assertEqual(self.estado(), previo)

    def test_reabrir_con_todo_activo_queda_abierta(self):
        self._cerrar_b_y()
        editar_sesion(self.b, usuario=None, fin=None)
        self.b.refresh_from_db()
        self.assertIsNone(self.b.fin)

    def test_sesion_abierta_con_cuenta_activa_reasigna_sus_cobros(self):
        otra = self.pos(self.rodrigo)
        c = self.cobro(h(13))
        editar_sesion(self.b, usuario=None, cuenta=otra)
        c.refresh_from_db()
        self.assertEqual(c.cuenta, otra)

    def test_editar_inicio_para_superponer_muestra_el_mensaje_de_superposicion(self):
        msg = "La sesión se superpone con otra sesión de la terminal."
        with self.assertRaisesMessage(ValidationError, msg):
            editar_sesion(self.b, usuario=None, inicio=h(11, 59, 59))
        editar_sesion(self.b, usuario=None, inicio=h(12, 0, 1))

    def test_inicio_en_el_futuro_se_rechaza_y_justo_ahora_pasa(self):
        with self.assertRaisesMessage(ValidationError, "El inicio no puede estar en el futuro."):
            editar_sesion(self.b, usuario=None, inicio=h(14, 0, 1))
        editar_sesion(self.b, usuario=None, inicio=h(14))

    def test_fin_en_el_futuro_se_rechaza(self):
        with self.assertRaisesMessage(ValidationError, "El fin no puede estar en el futuro."):
            editar_sesion(self.a, usuario=None, fin=h(14, 0, 1))

    def test_sin_cambios_no_genera_evento(self):
        antes = self.eventos(self.a, Evento.Tipo.CAMBIO)
        editar_sesion(self.a, usuario=None, inicio=h(9), cuenta=self.cuenta_a)
        self.assertEqual(self.eventos(self.a, Evento.Tipo.CAMBIO), antes)

    def test_adelantar_inicio_reasigna_cobros_que_quedan_en_sesion_anterior(self):
        # a 9-12, b desde 12: se acorta A para que empiece 10 y un cobro de 9:30 queda sin sesión
        self.cobro(h(9, 30))
        with self.assertRaisesMessage(ValidationError, "queda sin sesión"):
            editar_sesion(self.a, usuario=None, inicio=h(10))
        editar_sesion(self.a, usuario=None, inicio=h(9, 30))

    def test_evento_de_cambio_con_usuario(self):
        editar_sesion(self.a, usuario=None, inicio=h(8))
        ev = historial_de(self.a).filter(tipo=Evento.Tipo.CAMBIO).latest("pk")
        self.assertEqual({x["campo"] for x in ev.cambios}, {"inicio"})


class BorrarSesionTest(CorreccionBase):
    def test_con_cobro_cancelado_se_rechaza(self):
        self.cobro(h(10), estado="cancelado")
        with self.assertRaisesMessage(
            ValidationError, "La sesión tiene cobros. No se puede borrar."
        ):
            borrar_sesion(self.a, usuario=None)
        self.assertTrue(Sesion.objects.filter(pk=self.a.pk).exists())

    def test_sin_cobros_se_borra_con_un_evento_borrado(self):
        pk = self.a.pk
        borrar_sesion(self.a, usuario=None)
        self.assertFalse(Sesion.objects.filter(pk=pk).exists())
        self.assertEqual(
            Evento.objects.filter(
                entidad="terminales.sesion", entidad_id=pk, tipo=Evento.Tipo.BORRADO
            ).count(),
            1,
        )

    def test_cobro_de_otra_terminal_no_impide_borrar(self):
        otra = Terminal.objects.create(nombre="Otra", proveedor=self.mp, acepta_debito=True)
        self.cobro(h(10), cuenta=self.cuenta_a, terminal=otra)
        borrar_sesion(self.a, usuario=None)

    def test_cobro_justo_en_fin_de_sesion_cerrada_no_cuenta_y_un_segundo_antes_si(self):
        self.cobro(h(12), cuenta=self.cuenta_b)  # pertenece a B
        self.cobro(h(8, 59, 59), cuenta=self.cuenta_a)  # antes del inicio de A
        borrar_sesion(self.a, usuario=None)

    def test_cobro_en_el_inicio_de_la_sesion_impide_borrar(self):
        self.cobro(h(9))
        with self.assertRaisesMessage(ValidationError, "La sesión tiene cobros"):
            borrar_sesion(self.a, usuario=None)

    def test_sesion_abierta_con_cobro_posterior_se_rechaza(self):
        self.cobro(h(13))
        with self.assertRaisesMessage(ValidationError, "La sesión tiene cobros"):
            borrar_sesion(self.b, usuario=None)
