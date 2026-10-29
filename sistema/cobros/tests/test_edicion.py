from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError

from sistema.asignacion.servicios import lista_con_cupo
from sistema.auditoria.servicios import historial_de
from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.cobros.tests.test_comprobante import imagen
from sistema.cobros.tests.test_efectivo import AHORA, ARG, TRATAMIENTOS, item
from sistema.cobros.tests.test_pos import AVISAR, BasePos
from sistema.cobros.tests.test_transferencia import BaseTransferencia
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
NOV = datetime(2026, 11, 1, 10, 0, tzinfo=ARG)
OCT_31 = datetime(2026, 10, 31, 15, 0, tzinfo=ARG)


class BaseEdicion(BaseTransferencia):
    def final(self, monto="50000.00", items=None, reloj=AHORA):
        with mock.patch("django.utils.timezone.now", return_value=reloj):
            cobro = self.alta(monto=monto, items=items)
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        cobro.refresh_from_db()
        return cobro

    def editar(self, cobro, estado_visto="finalizado", usuario=None, **kw):
        return servicios.editar_cobro(
            cobro, usuario=usuario or self.admin, estado_visto=estado_visto, **kw
        )

    def rechaza(self, mensaje, cobro, **kw):
        with self.assertRaisesMessage(ValidationError, mensaje):
            self.editar(cobro, **kw)


class ExcedenteFechaCuentaTest(BaseEdicion):
    def test_us6_1_subir_el_monto_sobre_el_tope_marca_excedente_y_pierde_revisado(self):
        saldos = [tratamiento([prestacion(1, "80000", "0"), prestacion(2, "10000", "0")])]
        with mock.patch(TRATAMIENTOS, return_value=saldos):
            cobro = self.final(items=[item(1, "40000.00"), item(2, "10000.00")])
            self.assertFalse(cobro.excedente)
            Cobro.objects.filter(pk=cobro.pk).update(revisado=True)
            primero = cobro.items.get(numero=1)
            self.editar(cobro, monto=D("70000.00"), items=[{"id": primero.pk, "monto": D("60000")}])
        cobro.refresh_from_db()
        self.assertEqual(cobro.monto, D("70000.00"))
        self.assertEqual((cobro.excedente, cobro.revisado), (True, False))

    def test_monto_justo_en_el_tope_no_es_excedente_y_un_centavo_mas_si(self):
        saldos = [tratamiento([prestacion(1, "80000", "0")])]
        with mock.patch(TRATAMIENTOS, return_value=saldos):
            cobro = self.final("40000.00", [item(1, "40000.00")])
            it = cobro.items.get()
            self.editar(cobro, monto=D("60000.00"), items=[{"id": it.pk, "monto": D("60000.00")}])
            cobro.refresh_from_db()
            self.assertFalse(cobro.excedente)
            self.editar(cobro, monto=D("60000.01"), items=[{"id": it.pk, "monto": D("60000.01")}])
        cobro.refresh_from_db()
        self.assertTrue(cobro.excedente)

    def test_us6_2_cambiar_la_fecha_al_mes_siguiente_mueve_el_cupo(self):
        cobro = self.final(reloj=OCT_31)
        octubre, noviembre = OCT_31, NOV
        self.assertEqual(lista_con_cupo(octubre)[0].cupo_mensual, D("10000.00"))
        self.editar(cobro, fecha=NOV)
        self.assertEqual(lista_con_cupo(octubre)[0].cupo_mensual, D("60000.00"))
        self.assertEqual(lista_con_cupo(noviembre)[0].cupo_mensual, D("10000.00"))

    def test_us6_6_cambiar_de_cuenta_evalua_el_excedente_en_la_nueva(self):
        otra = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=crear_destinatario(usuario=None, nombre="Rodrigo", tipo="clinica"),
            alias="rodrigo.mp",
            tope_mensual=D("30000.00"),
        )
        cobro = self.final()
        posiciones = list(self.cuenta.__class__.objects.values_list("pk", "posicion"))
        self.editar(cobro, cuenta=otra)
        cobro.refresh_from_db()
        self.assertEqual((cobro.cuenta, cobro.destinatario), (otra, otra.destinatario))
        self.assertTrue(cobro.excedente)
        self.assertEqual(
            posiciones, list(self.cuenta.__class__.objects.values_list("pk", "posicion"))
        )
        self.editar(cobro, cuenta=self.cuenta)
        cobro.refresh_from_db()
        self.assertFalse(cobro.excedente)

    def test_cuenta_que_no_es_de_transferencia_se_rechaza(self):
        cobro = self.final()
        self.rechaza("Elegí una cuenta de transferencia.", cobro, cuenta=self.efectivo)

    def test_cuenta_archivada_se_acepta(self):
        otra = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="vieja.uno",
            tope_mensual=D("90000.00"),
            estado="archivada",
        )
        cobro = self.final()
        self.editar(cobro, cuenta=otra)
        cobro.refresh_from_db()
        self.assertEqual(cobro.cuenta, otra)


class PosEdicionTest(BasePos):
    def test_us6_3_hora_sin_sesion_rechaza_y_la_fecha_no_cambia(self):
        cobro = self.pos()
        con_hora = cobro.fecha
        with self.assertRaisesMessage(
            ValidationError, "La terminal no tiene una sesión en esa fecha y hora."
        ):
            servicios.editar_cobro(
                cobro,
                usuario=self.admin,
                estado_visto="finalizado",
                fecha=AHORA.replace(hour=8),
            )
        cobro.refresh_from_db()
        self.assertEqual(cobro.fecha, con_hora)

    def test_fecha_justo_en_el_inicio_de_la_sesion_se_acepta(self):
        cobro = self.pos()
        servicios.editar_cobro(
            cobro, usuario=self.admin, estado_visto="finalizado", fecha=AHORA.replace(hour=9)
        )
        cobro.refresh_from_db()
        self.assertEqual(cobro.fecha, AHORA.replace(hour=9))

    def test_efectivo_a_debito_pide_terminal_datos_y_comprobante(self):
        cobro = self.crear(items=[item(1, "30000.00")], monto="30000.00")

        def pide(mensaje):
            return self.assertRaisesMessage(ValidationError, mensaje)

        with pide("Elegí la terminal POS."):
            self.edita(cobro, metodo="debito")
        with pide("Cargá el banco emisor"):
            self.edita(
                cobro,
                metodo="debito",
                terminal=self.smart,
                monto_neto=D("29000.00"),
                numero_operacion="1",
            )
        with pide("Falta el comprobante."):
            self.edita(cobro, **self.datos_debito())
        with mock.patch(AVISAR) as avisar:
            self.edita(cobro, comprobante=imagen(), **self.datos_debito())
        cobro.refresh_from_db()
        self.assertEqual((cobro.metodo, cobro.cuenta), ("debito", self.cuenta))
        self.assertEqual(cobro.destinatario, self.cuenta.destinatario)
        avisar.assert_called_once_with(self.cuenta, D("0.00"))

    def test_debito_a_efectivo_usa_la_caja_y_limpia_los_datos_pos(self):
        cobro = self.pos()
        self.edita(cobro, metodo="efectivo")
        cobro.refresh_from_db()
        self.assertEqual((cobro.metodo, cobro.cuenta), ("efectivo", self.efectivo))
        self.assertEqual(
            (cobro.terminal, cobro.monto_neto, cobro.numero_operacion, cobro.banco_emisor),
            (None, None, None, None),
        )
        self.assertIsNone(cobro.ultimos_4)

    def test_debito_a_qr_limpia_banco_y_ultimos_4(self):
        cobro = self.pos()
        self.edita(cobro, metodo="qr")
        cobro.refresh_from_db()
        self.assertEqual((cobro.metodo, cobro.banco_emisor, cobro.ultimos_4), ("qr", None, None))

    def test_banco_no_vigente_se_rechaza(self):
        cobro = self.pos()
        self.banco.vigente = False
        self.banco.save()
        with self.assertRaisesMessage(ValidationError, "Elegí un banco vigente"):
            self.edita(cobro, banco_emisor=self.banco)

    def test_operacion_repetida_se_rechaza(self):
        self.pos()
        otro = self.pos(numero_operacion="2")
        with self.assertRaisesMessage(ValidationError, "Ya hay un cobro con ese número"):
            self.edita(otro, numero_operacion="88213")

    def datos_debito(self):
        return {
            "metodo": "debito",
            "terminal": self.smart,
            "monto_neto": D("29000.00"),
            "numero_operacion": "1",
            "banco_emisor": self.banco,
            "ultimos_4": "1234",
        }

    def edita(self, cobro, **kw):
        return servicios.editar_cobro(cobro, usuario=self.admin, estado_visto=cobro.estado, **kw)

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_user(
            "adm2", email="adm2@example.com", role="administrator"
        )


class ReglasDeEdicionTest(BaseEdicion):
    def test_pendiente_con_otro_metodo_se_rechaza(self):
        cobro = self.alta()
        self.rechaza(
            "Un cobro Pendiente no cambia de método de cobro. "
            "Cancelalo con «Se cobró por otro medio» y creá otro.",
            cobro,
            estado_visto="pendiente",
            metodo="efectivo",
        )

    def test_cancelado_solo_acepta_observaciones(self):
        cobro = self.alta()
        self.cancelar(cobro)
        self.rechaza(
            "Un cobro Cancelado solo acepta cambios en observaciones.",
            cobro,
            estado_visto="cancelado",
            monto=D("1.00"),
        )
        self.editar(cobro, estado_visto="cancelado", observaciones="Se arrepintió")
        cobro.refresh_from_db()
        self.assertEqual((cobro.estado, cobro.observaciones), ("cancelado", "Se arrepintió"))

    def test_campo_no_editable_se_rechaza(self):
        cobro = self.final()
        self.rechaza("El campo creado_por no se edita.", cobro, creado_por=self.admin)

    def test_el_paciente_no_cambia(self):
        cobro = self.final()
        self.rechaza(
            "El paciente no cambia. Cancelá el cobro y creá otro.",
            cobro,
            id_paciente_dentalink=999,
        )

    def test_operador_no_edita_ni_marca_revisado(self):
        cobro = self.final()
        with self.assertRaises(PermissionDenied):
            self.editar(cobro, usuario=self.ana, observaciones="x")
        with self.assertRaises(PermissionDenied):
            servicios.marcar_revisado(cobro, usuario=self.ana, comentario="ok")

    def test_estado_visto_viejo_se_rechaza(self):
        cobro = self.alta()
        self.rechaza("El cobro cambió a Pendiente.", cobro, estado_visto="finalizado")

    def test_monto_distinto_de_la_suma_de_items_se_rechaza(self):
        cobro = self.final()
        self.rechaza("no es igual a la suma", cobro, monto=D("50000.01"))
        self.rechaza("no es igual a la suma", cobro, monto=D("49999.99"))

    def test_monto_cero_se_rechaza(self):
        cobro = self.final()
        with self.assertRaises(ValidationError):
            self.editar(cobro, monto=D("0.00"))


class RevisadoTest(BaseEdicion):
    def test_us6_5_marcar_revisado_guarda_comentario_y_evento(self):
        cobro = self.final()
        Cobro.objects.filter(pk=cobro.pk).update(excedente=True)
        servicios.marcar_revisado(
            cobro, usuario=self.admin, comentario="Rodrigo devuelve la diferencia"
        )
        cobro.refresh_from_db()
        self.assertEqual((cobro.excedente, cobro.revisado), (True, True))
        self.assertEqual(cobro.comentario_revision, "Rodrigo devuelve la diferencia")
        ultimo = historial_de(cobro).filter(tipo="cambio").order_by("-pk").first()
        self.assertEqual({c["campo"] for c in ultimo.cambios}, {"revisado", "comentario_revision"})

    def test_comentario_vacio_o_en_blanco_se_rechaza_con_excedente(self):
        cobro = self.final()
        Cobro.objects.filter(pk=cobro.pk).update(excedente=True)
        for comentario in ("", "   "):
            with self.assertRaisesMessage(ValidationError, "Escribí un comentario."):
                servicios.marcar_revisado(cobro, usuario=self.admin, comentario=comentario)

    def test_us6_4_cancelar_finalizado_con_items_enviados_pide_ajuste_manual(self):
        cobro = self.final()
        cobro.items.update(estado_envio="enviado")
        servicios.cancelar_cobro(
            cobro, usuario=self.admin, motivo="datos_erroneos", estado_visto="finalizado"
        )
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "cancelado")
        self.assertEqual(
            set(cobro.items.values_list("estado_envio", flat=True)), {"requiere_ajuste"}
        )


class ItemsYComprobanteTest(BaseEdicion):
    def test_reemplazo_de_comprobante_deja_el_viejo_y_registra_ambos_nombres(self):
        cobro = self.final()
        viejo = cobro.comprobante.name
        self.editar(cobro, comprobante=imagen(nombre="otro.png"))
        cobro.refresh_from_db()
        self.assertNotEqual(cobro.comprobante.name, viejo)
        self.assertTrue(cobro.comprobante.storage.exists(viejo))
        cambio = historial_de(cobro).filter(tipo="cambio").order_by("-pk").first()
        linea = next(c for c in cambio.cambios if c["campo"] == "comprobante")
        self.assertEqual((linea["anterior"], linea["nuevo"]), (viejo, cobro.comprobante.name))

    def test_sin_comprobante_nuevo_queda_el_actual(self):
        cobro = self.final()
        viejo = cobro.comprobante.name
        self.editar(cobro, observaciones="x")
        cobro.refresh_from_db()
        self.assertEqual(cobro.comprobante.name, viejo)

    def test_item_se_edita_hasta_el_saldo_y_un_centavo_mas_se_rechaza(self):
        cobro = self.final("30000.00", [item(1, "30000.00")])
        it = cobro.items.get()
        self.editar(cobro, monto=D("40000.00"), items=[{"id": it.pk, "monto": D("40000.00")}])
        it.refresh_from_db()
        self.assertEqual(it.monto, D("40000.00"))
        self.rechaza(
            "supera el saldo disponible",
            cobro,
            monto=D("40000.01"),
            items=[{"id": it.pk, "monto": D("40000.01")}],
        )
        it.refresh_from_db()
        self.assertEqual(it.monto, D("40000.00"))

    def test_item_nuevo_de_otra_prestacion_se_acepta(self):
        cobro = self.final("40000.00", [item(1, "40000.00")])
        self.editar(
            cobro,
            monto=D("65000.00"),
            items=[{"id_prestacion": 2, "monto": D("25000.00"), "id_profesional": None}],
        )
        self.assertEqual(
            list(cobro.items.order_by("numero").values_list("numero", "id_prestacion")),
            [(1, 1), (2, 2)],
        )

    def test_item_enviado_editado_pide_ajuste_manual(self):
        cobro = self.final("30000.00", [item(1, "30000.00")])
        cobro.items.update(estado_envio="enviado")
        it = cobro.items.get()
        self.editar(cobro, monto=D("35000.00"), items=[{"id": it.pk, "monto": D("35000.00")}])
        it.refresh_from_db()
        self.assertEqual((it.estado_envio, it.monto), ("requiere_ajuste", D("35000.00")))

    def test_item_de_otro_cobro_se_rechaza(self):
        cobro = self.final("30000.00", [item(1, "30000.00")])
        with self.assertRaisesMessage(ValidationError, "El ítem no pertenece al cobro."):
            self.editar(cobro, items=[{"id": 999999, "monto": D("1.00")}])

    def test_sc005_historial_con_las_lineas_del_cambio(self):
        cobro = self.final("30000.00", [item(1, "30000.00")])
        it = cobro.items.get()
        self.editar(
            cobro,
            fecha=OCT_31,
            monto=D("35000.00"),
            observaciones="Corregido",
            items=[{"id": it.pk, "monto": D("35000.00")}],
        )
        cambio = historial_de(cobro).filter(tipo="cambio").order_by("-pk").first()
        por_campo = {c["campo"]: c for c in cambio.cambios}
        self.assertEqual(set(por_campo), {"fecha", "monto", "observaciones"})
        self.assertEqual(por_campo["monto"]["anterior"], "30000.00")
        self.assertEqual(por_campo["monto"]["nuevo"], "35000.00")
        evento_item = historial_de(it).filter(tipo="cambio").get()
        self.assertEqual([c["campo"] for c in evento_item.cambios], ["monto"])
