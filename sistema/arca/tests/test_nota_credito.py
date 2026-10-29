from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils.timezone import localtime

from sistema.arca import servicios as arca
from sistema.arca.models import ComprobanteFiscal
from sistema.arca.cliente import Resultado
from sistema.arca.servicios import FaltaDecision
from sistema.arca.tests.fabricas import (
    CAE,
    ArcaFalso,
    datos_de_arca,
    emisor_prueba,
    factura_prueba,
)
from sistema.arca.tests.test_nace_factura import Fixture, activar_facturacion
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import foto, historial_de
from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.core.tiempo import ARG
from sistema.cobros.tests.test_efectivo import AHORA, TRATAMIENTOS, BaseEfectivo
from sistema.dentalink.tests.test_lectura import prestacion, tratamiento
from sistema.destinatarios.models import Destinatario
from sistema.terminales.servicios import corregir_cambio
from sistema.terminales.tests.test_correccion import CorreccionBase, h

D = Decimal
PREGUNTA_CANCELAR = "¿Emitir nota de crédito por $65.000,00?"
PREGUNTA_EDITAR = "¿Emitir nota de crédito por $65.000,00 y una factura nueva?"
EMITIR = "sistema.arca.tasks.emitir.delay"


class Base(Fixture, BaseEfectivo):
    """Efectivo de $65.000,00 de "Clínica" con facturación: una factura Emitida número 42."""

    def setUp(self):
        super().setUp()
        p = mock.patch(
            TRATAMIENTOS,
            return_value=[tratamiento([prestacion(1, "80000", "0"), prestacion(2, "25000", "0")])],
        )
        p.start()
        self.addCleanup(p.stop)
        self.admin = get_user_model().objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        self.emisor = activar_facturacion(self.efectivo.destinatario)
        self.arca = ArcaFalso(self)

    def facturado(self, emitir=True):
        cobro = self.crear()
        factura = ComprobanteFiscal.objects.get()
        if emitir:
            self.assertEqual(arca.emitir(factura.pk), "emitido")
        factura.refresh_from_db()
        return cobro, factura

    def cancelar(self, cobro, usuario=None, **kw):
        return servicios.cancelar_cobro(
            cobro,
            usuario=usuario or self.admin,
            motivo="otro_medio",
            estado_visto="finalizado",
            **kw,
        )

    def editar(self, cobro, **kw):
        return servicios.editar_cobro(cobro, usuario=self.admin, estado_visto="finalizado", **kw)

    def subir_monto(self, cobro, **kw):
        """Sube el ítem 1 de $40.000,00 a $45.000,00: el cobro pasa de $65.000,00 a $70.000,00."""
        primero = cobro.items.get(numero=1)
        return self.editar(
            cobro, monto=D("70000.00"), items=[{"id": primero.pk, "monto": D("45000.00")}], **kw
        )

    def notas(self):
        return ComprobanteFiscal.objects.filter(tipo="nota_credito")

    def facturas(self):
        return ComprobanteFiscal.objects.filter(tipo="factura")


class CancelarTest(Base):
    def test_sin_respuesta_pregunta_con_el_monto_y_el_cobro_sigue_finalizado(self):
        cobro, factura = self.facturado()
        with self.assertRaises(FaltaDecision) as c:
            self.cancelar(cobro)
        self.assertEqual(c.exception.pregunta, PREGUNTA_CANCELAR)
        self.assertEqual(c.exception.messages, [PREGUNTA_CANCELAR])
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")
        self.assertEqual(self.notas().count(), 0)
        self.assertTrue(self.facturas().get().vigente)

    def test_si_cancela_y_crea_la_nota_de_credito_asociada_del_mismo_emisor(self):
        cobro, factura = self.facturado()
        with self.captureOnCommitCallbacks(execute=True):
            self.cancelar(cobro, nota_credito=True)
        cobro.refresh_from_db()
        factura.refresh_from_db()
        (nota,) = self.notas()
        self.assertEqual(cobro.estado, "cancelado")
        self.assertEqual((nota.asociado, nota.emisor, nota.cobro), (factura, self.emisor, cobro))
        self.assertEqual((nota.estado, nota.vigente), ("pendiente", True))
        self.assertFalse(factura.vigente)
        self.assertFalse(factura.a_revisar)
        self.emitir.assert_called_with(nota.pk)
        evento = historial_de(factura).filter(tipo="cambio").latest("pk")
        self.assertEqual(evento.detalle, "Nota de crédito: sí")
        self.assertEqual(evento.usuario, self.admin)
        self.assertEqual(self.facturas().count(), 1)  # cancelar no crea factura nueva

    def test_la_nota_de_credito_se_emite_tipo_8_con_la_factura_asociada_y_el_total(self):
        cobro, factura = self.facturado()
        self.cancelar(cobro, nota_credito=True)
        (nota,) = self.notas()
        self.assertEqual(arca.emitir(nota.pk), "emitido")
        nota.refresh_from_db()
        pedido = self.arca.solicitar_cae.call_args.args[2]
        self.assertEqual(pedido["cbte_tipo"], 8)
        self.assertEqual(pedido["total"], D("65000.00"))
        self.assertEqual(
            pedido["asociado"],
            {
                "tipo": 6,
                "punto_venta": 3,
                "numero": 42,
                "cuit": "20000000001",
                "fecha": factura.fecha,
            },
        )
        self.assertEqual(
            (nota.total, nota.neto, nota.iva), (D("65000.00"), D("53719.01"), D("11280.99"))
        )
        self.assertEqual((nota.cbte_tipo, nota.numero), (8, 43))

    def test_no_cancela_deja_la_factura_vigente_y_registra_la_decision(self):
        cobro, factura = self.facturado()
        self.cancelar(cobro, nota_credito=False)
        cobro.refresh_from_db()
        factura.refresh_from_db()
        self.assertEqual(cobro.estado, "cancelado")
        self.assertTrue(factura.vigente)
        self.assertEqual(self.notas().count(), 0)
        evento = historial_de(factura).filter(tipo="cambio").latest("pk")
        self.assertEqual(evento.detalle, "Nota de crédito: no")
        self.assertEqual(evento.usuario, self.admin)

    def test_despues_del_no_el_administrador_puede_emitir_la_nota_una_sola_vez(self):
        cobro, factura = self.facturado()
        self.cancelar(cobro, nota_credito=False)
        arca.emitir_nota_credito(cobro, usuario=self.admin)
        (nota,) = self.notas()
        self.assertEqual(nota.asociado, factura)
        with self.assertRaisesMessage(
            ValidationError, "El cobro no tiene una factura emitida vigente."
        ):
            arca.emitir_nota_credito(cobro, usuario=self.admin)
        self.assertEqual(self.notas().count(), 1)

    def test_nota_de_credito_con_la_facturacion_del_destinatario_desactivada_sale_del_mismo_emisor(
        self,
    ):
        cobro, factura = self.facturado()
        Destinatario.objects.filter(pk=cobro.destinatario_id).update(
            facturacion_activa=False, emisor=None
        )
        self.cancelar(cobro, nota_credito=True)
        (nota,) = self.notas()
        self.assertEqual(nota.emisor, self.emisor)
        self.assertEqual(arca.emitir(nota.pk), "emitido")

    def test_cancelar_con_factura_pendiente_la_descarta_sin_preguntar(self):
        cobro, factura = self.facturado(emitir=False)
        self.cancelar(cobro)
        factura.refresh_from_db()
        self.assertEqual((factura.estado, factura.vigente), ("descartado", False))
        self.assertEqual(self.notas().count(), 0)
        self.assertEqual(arca.emitir(factura.pk), "omitido")
        self.assertTrue(self.arca.no_llamo_a_arca())

    def test_cancelar_con_factura_con_error_la_descarta(self):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(estado="error", motivo="ARCA cayó")
        self.cancelar(cobro)
        factura.refresh_from_db()
        self.assertEqual((factura.estado, factura.vigente), ("descartado", False))

    def test_cancelar_con_factura_en_curso_se_rechaza_y_no_cambia_nada(self):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(en_curso_desde=AHORA)
        with self.assertRaisesMessage(
            ValidationError, "La factura del cobro se está emitiendo. Probá de nuevo en un minuto."
        ):
            self.cancelar(cobro)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")

    def test_cancelar_con_factura_dudosa_pendiente_se_rechaza(self):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(numero_intentado=42)
        with self.assertRaisesMessage(ValidationError, "se está emitiendo"):
            self.cancelar(cobro)

    def test_cancelar_con_factura_dudosa_en_error_pide_reintentar(self):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(
            numero_intentado=42, estado="error", motivo="ARCA no respondió"
        )
        with self.assertRaisesMessage(
            ValidationError,
            "La factura del cobro quedó sin confirmar en ARCA. Reintentala antes de cambiar el cobro.",
        ):
            self.cancelar(cobro)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")

    def test_cancelar_un_cobro_sin_factura_no_pregunta(self):
        Destinatario.objects.update(facturacion_activa=False, emisor=None)
        cobro = self.crear()
        self.cancelar(cobro)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "cancelado")


class EditarTest(Base):
    def test_subir_el_monto_sin_respuesta_pregunta_con_las_dos_cifras(self):
        cobro, factura = self.facturado()
        with self.assertRaises(FaltaDecision) as c:
            self.subir_monto(cobro)
        self.assertEqual(c.exception.pregunta, PREGUNTA_EDITAR)
        cobro.refresh_from_db()
        self.assertEqual(cobro.monto, D("65000.00"))  # todo se deshizo
        self.assertEqual(cobro.items.get(numero=1).monto, D("40000.00"))
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)

    def test_si_emite_la_nota_de_65000_y_una_factura_nueva_de_70000(self):
        cobro, factura = self.facturado()
        with self.captureOnCommitCallbacks(execute=True):
            self.subir_monto(cobro, nota_credito=True)
        factura.refresh_from_db()
        (nota,) = self.notas()
        nueva = self.facturas().get(vigente=True)
        self.assertFalse(factura.vigente)
        self.assertEqual(nota.asociado, factura)
        self.assertNotEqual(nueva.pk, factura.pk)
        self.assertEqual(nueva.estado, "pendiente")
        self.assertEqual(self.emitir.call_count, 2)  # la nota y la factura nueva
        self.assertEqual(arca.emitir(nota.pk), "emitido")
        self.assertEqual(arca.emitir(nueva.pk), "emitido")
        nota.refresh_from_db()
        nueva.refresh_from_db()
        self.assertEqual(nota.total, D("65000.00"))
        self.assertEqual(nueva.total, D("70000.00"))
        self.assertEqual((nueva.neto, nueva.iva), (D("57851.24"), D("12148.76")))

    def test_la_factura_nueva_espera_a_la_nota_de_credito_y_no_llama_a_arca(self):
        cobro, _ = self.facturado()
        self.subir_monto(cobro, nota_credito=True)
        nueva = self.facturas().get(vigente=True)
        llamadas = self.arca.solicitar_cae.call_count
        self.assertEqual(arca.emitir(nueva.pk), "omitido")
        self.assertEqual(self.arca.solicitar_cae.call_count, llamadas)
        nueva.refresh_from_db()
        self.assertEqual((nueva.estado, nueva.intentos), ("pendiente", 0))

    def test_nota_rechazada_la_factura_nueva_no_sale_y_el_cobro_queda_marcado(self):
        cobro, _ = self.facturado()
        self.subir_monto(cobro, nota_credito=True)
        (nota,) = self.notas()
        nueva = self.facturas().get(vigente=True)
        self.arca.responder(Resultado("rechazado", error="10016: dato inválido"))
        self.assertEqual(arca.emitir(nota.pk), "error")
        self.assertEqual(arca.emitir(nueva.pk), "omitido")
        nueva.refresh_from_db()
        self.assertEqual(nueva.estado, "pendiente")
        self.assertEqual(self.facturas().filter(estado="emitido").count(), 1)
        self.assertIn("Factura con error", arca.marcas(Cobro.objects.get(pk=cobro.pk)))

    def test_nota_reintentada_y_emitida_encola_la_factura_nueva_una_sola_vez(self):
        cobro, _ = self.facturado()
        self.subir_monto(cobro, nota_credito=True)
        (nota,) = self.notas()
        nueva = self.facturas().get(vigente=True)
        self.arca.responder(Resultado("rechazado", error="10016: dato inválido"))
        arca.emitir(nota.pk)
        self.emitir.reset_mock()
        arca.reintentar(nota, usuario=self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(arca.emitir(nota.pk), "emitido")
        self.emitir.assert_any_call(nueva.pk)
        self.assertEqual(arca.emitir(nueva.pk), "emitido")
        self.assertEqual(self.facturas().filter(estado="emitido").count(), 2)
        self.assertEqual(arca.emitir(nueva.pk), "omitido")  # nunca se emite dos veces

    def test_no_deja_la_factura_vigente_y_registra_la_decision(self):
        cobro, factura = self.facturado()
        self.subir_monto(cobro, nota_credito=False)
        factura.refresh_from_db()
        cobro.refresh_from_db()
        self.assertEqual(cobro.monto, D("70000.00"))
        self.assertTrue(factura.vigente)
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)
        evento = historial_de(factura).filter(tipo="cambio").latest("pk")
        self.assertEqual(evento.detalle, "Nota de crédito: no")

    def test_solo_observaciones_no_pregunta(self):
        cobro, factura = self.facturado()
        self.editar(cobro, observaciones="Se corrigió una nota")
        self.assertEqual(historial_de(factura).filter(tipo="cambio").count(), 0)

    def test_cambiar_la_fecha_no_pregunta(self):
        cobro, factura = self.facturado()
        self.editar(cobro, fecha=AHORA - timedelta(days=1))
        cobro.refresh_from_db()
        self.assertEqual(cobro.fecha, AHORA - timedelta(days=1))
        self.assertEqual(self.notas().count(), 0)

    def test_el_monto_un_centavo_mayor_pregunta_y_el_mismo_monto_no(self):
        cobro, factura = self.facturado()
        primero = cobro.items.get(numero=1)
        self.editar(cobro, monto=D("65000.00"), items=[{"id": primero.pk, "monto": D("40000.00")}])
        with self.assertRaises(FaltaDecision) as c:
            self.editar(
                cobro, monto=D("65000.01"), items=[{"id": primero.pk, "monto": D("40000.01")}]
            )
        self.assertEqual(
            c.exception.pregunta,
            "¿Emitir nota de crédito por $65.000,00 y una factura nueva?",
        )

    def test_el_monto_un_centavo_menor_tambien_pregunta(self):
        cobro, factura = self.facturado()
        primero = cobro.items.get(numero=1)
        with self.assertRaises(FaltaDecision):
            self.editar(
                cobro, monto=D("64999.99"), items=[{"id": primero.pk, "monto": D("39999.99")}]
            )

    def test_editar_observaciones_no_factura_un_cobro_de_antes_de_activar_la_facturacion(self):
        # FR-008: la facturación rige "en ese momento", cuando el cobro pasa a Finalizado.
        Destinatario.objects.update(facturacion_activa=False, emisor=None)
        cobro = self.crear()
        Destinatario.objects.filter(pk=cobro.destinatario_id).update(
            facturacion_activa=True, emisor=self.emisor
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.editar(cobro, observaciones="Otra nota")
        self.assertEqual(ComprobanteFiscal.objects.count(), 0)

    def test_editar_observaciones_despues_de_una_nota_de_credito_sola_no_factura_de_nuevo(self):
        cobro, factura = self.facturado()
        arca.emitir_nota_credito(cobro, usuario=self.admin)  # FR-018, sin factura nueva
        self.editar(cobro, observaciones="Otra nota")
        self.assertEqual(self.facturas().count(), 1)
        self.assertIsNone(arca.factura_vigente(cobro))

    def test_editar_con_la_factura_vigente_no_crea_otra(self):
        cobro, factura = self.facturado(emitir=False)
        self.editar(cobro, observaciones="Otra nota")
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)


class CambioDeDestinatarioTest(Base):
    """`al_cambiar_cobro` con un destinatario distinto (R9), sin pasar por una pantalla."""

    def setUp(self):
        super().setUp()
        self.otro = Destinatario.objects.create(nombre="Rodrigo", tipo="clinica")
        self.emisor_otro = emisor_prueba(cuit="20000000002", razon_social="Rodrigo S.R.L.")
        Destinatario.objects.filter(pk=self.otro.pk).update(
            facturacion_activa=True, emisor=self.emisor_otro
        )

    def mover(self, cobro, destinatario, usuario, **kw):
        antes = foto(cobro)
        cobro.destinatario = destinatario
        cobro.save()
        arca.al_cambiar_cobro(cobro, antes, usuario=usuario, **kw)

    def test_factura_pendiente_toma_el_emisor_del_destinatario_nuevo(self):
        cobro, factura = self.facturado(emitir=False)
        self.mover(cobro, Destinatario.objects.get(pk=self.otro.pk), self.admin)
        factura.refresh_from_db()
        self.assertEqual((factura.estado, factura.emisor), ("pendiente", self.emisor_otro))
        self.assertTrue(factura.vigente)

    def test_factura_pendiente_se_descarta_si_el_destinatario_nuevo_no_factura(self):
        Destinatario.objects.filter(pk=self.otro.pk).update(facturacion_activa=False, emisor=None)
        cobro, factura = self.facturado(emitir=False)
        self.mover(cobro, Destinatario.objects.get(pk=self.otro.pk), self.admin)
        factura.refresh_from_db()
        self.assertEqual((factura.estado, factura.vigente), ("descartado", False))

    def test_factura_emitida_pregunta_sin_la_parte_nueva_si_el_destinatario_nuevo_no_factura(self):
        Destinatario.objects.filter(pk=self.otro.pk).update(facturacion_activa=False, emisor=None)
        cobro, factura = self.facturado()
        with self.assertRaises(FaltaDecision) as c:
            self.mover(cobro, Destinatario.objects.get(pk=self.otro.pk), self.admin)
        self.assertEqual(
            c.exception.pregunta,
            "¿Emitir nota de crédito por $65.000,00? No se emite factura nueva: el destinatario no tiene la facturación activa.",
        )

    def test_factura_emitida_con_si_crea_la_nota_del_emisor_viejo_y_factura_del_nuevo(self):
        cobro, factura = self.facturado()
        self.mover(cobro, Destinatario.objects.get(pk=self.otro.pk), self.admin, nota_credito=True)
        (nota,) = self.notas()
        nueva = self.facturas().get(vigente=True)
        self.assertEqual(nota.emisor, self.emisor)
        self.assertEqual(nueva.emisor, self.emisor_otro)

    def test_el_sistema_marca_a_revisar_una_factura_emitida_y_no_pregunta(self):
        cobro, factura = self.facturado()
        self.mover(
            cobro,
            Destinatario.objects.get(pk=self.otro.pk),
            None,
            origen="corrección de sesión",
        )
        factura.refresh_from_db()
        self.assertTrue(factura.a_revisar)
        self.assertTrue(factura.vigente)
        self.assertEqual(self.notas().count(), 0)
        evento = historial_de(factura).filter(tipo="cambio").latest("pk")
        self.assertEqual(evento.origen, "corrección de sesión")
        self.assertIsNone(evento.usuario)

    def marcar_sin_emitir(self, **campos):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(**campos)
        self.mover(cobro, Destinatario.objects.get(pk=self.otro.pk), None)
        factura.refresh_from_db()
        return factura

    def test_el_sistema_marca_a_revisar_una_factura_en_curso(self):
        factura = self.marcar_sin_emitir(en_curso_desde=AHORA)
        self.assertEqual((factura.a_revisar, factura.estado), (True, "pendiente"))

    def test_el_sistema_marca_a_revisar_una_factura_dudosa(self):
        factura = self.marcar_sin_emitir(numero_intentado=42)
        self.assertEqual((factura.a_revisar, factura.estado), (True, "pendiente"))

    def test_el_sistema_no_marca_si_solo_cambia_el_monto(self):
        cobro, factura = self.facturado()
        antes = foto(cobro)
        cobro.monto = D("65000.01")
        cobro.save()
        arca.al_cambiar_cobro(cobro, antes, usuario=None)
        factura.refresh_from_db()
        self.assertFalse(factura.a_revisar)


class ResolverARevisarTest(Base):
    def marcada(self):
        cobro, factura = self.facturado()
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(a_revisar=True)
        return cobro, factura

    def test_emitir_si_crea_la_nota_y_la_factura_nueva_y_quita_la_marca(self):
        cobro, factura = self.marcada()
        arca.resolver_a_revisar(cobro, usuario=self.admin, emitir=True)
        factura.refresh_from_db()
        self.assertFalse(factura.a_revisar)
        self.assertFalse(factura.vigente)
        self.assertEqual(self.notas().get().asociado, factura)
        self.assertEqual(self.facturas().get(vigente=True).estado, "pendiente")

    def test_emitir_no_quita_la_marca_y_deja_la_decision_en_el_historial(self):
        cobro, factura = self.marcada()
        arca.resolver_a_revisar(cobro, usuario=self.admin, emitir=False)
        factura.refresh_from_db()
        self.assertFalse(factura.a_revisar)
        self.assertTrue(factura.vigente)
        self.assertEqual(self.notas().count(), 0)
        evento = historial_de(factura).filter(tipo="cambio").latest("pk")
        self.assertEqual(evento.detalle, "Nota de crédito: no")

    def test_sin_marca_se_rechaza(self):
        cobro, factura = self.facturado()
        with self.assertRaisesMessage(ValidationError, "El cobro no tiene una factura a revisar."):
            arca.resolver_a_revisar(cobro, usuario=self.admin, emitir=False)

    def test_emitir_si_con_la_factura_sin_emitir_se_rechaza(self):
        cobro, factura = self.facturado(emitir=False)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(a_revisar=True)
        with self.assertRaisesMessage(ValidationError, "todavía no está emitida"):
            arca.resolver_a_revisar(cobro, usuario=self.admin, emitir=True)
        self.assertEqual(self.notas().count(), 0)

    def test_solo_el_administrador_resuelve_y_emite_la_nota(self):
        cobro, factura = self.marcada()
        operador = get_user_model().objects.create_user(
            "op", email="op@example.com", role="operator"
        )
        for usuario in (operador, mock.Mock(role="professional")):
            with self.subTest(rol=usuario.role):
                with self.assertRaises(PermissionDenied):
                    arca.resolver_a_revisar(cobro, usuario=usuario, emitir=True)
                with self.assertRaises(PermissionDenied):
                    arca.emitir_nota_credito(cobro, usuario=usuario)
        self.assertEqual(self.notas().count(), 0)


class NuncaDosVecesTest(Base):
    def test_nota_dudosa_consulta_antes_de_pedir_otro_numero(self):
        cobro, factura = self.facturado()
        arca.emitir_nota_credito(cobro, usuario=self.admin)
        (nota,) = self.notas()
        corte = Resultado("dudoso", error="ARCA no respondió a tiempo.")
        self.arca.responder(corte)
        self.assertEqual(arca.emitir(nota.pk), "pendiente")
        nota.refresh_from_db()
        self.assertEqual(nota.numero_intentado, 43)
        llamadas = self.arca.solicitar_cae.call_count
        self.arca.consultar.return_value = datos_de_arca(
            doc_numero=nota.cobro.dni_paciente, total=D("65000.00")
        )
        despues = nota.proximo_intento + timedelta(seconds=1)
        self.assertEqual(arca.emitir(nota.pk, ahora=despues), "emitido")
        nota.refresh_from_db()
        self.assertEqual((nota.numero, nota.cae, nota.cbte_tipo), (43, CAE, 8))
        self.assertEqual(self.arca.solicitar_cae.call_count, llamadas)  # no se pidió otro número
        self.assertEqual(self.arca.consultar.call_args.args[2:], (8, 43))
        self.assertEqual(arca.emitir(nota.pk, ahora=despues), "omitido")  # ya Emitida

    def test_emitir_la_nota_de_nuevo_no_crea_una_segunda(self):
        cobro, factura = self.facturado()
        arca.emitir_nota_credito(cobro, usuario=self.admin)
        with self.assertRaises(ValidationError):
            arca.emitir_nota_credito(cobro, usuario=self.admin)
        self.assertEqual(self.notas().count(), 1)


# --- corrección de sesión de 006 (FR-019 de 006) ---


class CorreccionDeSesionTest(CorreccionBase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        parche = mock.patch(EMITIR)
        parche.start()
        self.addCleanup(parche.stop)
        self.emisor_a = emisor_prueba()
        self.emisor_b = emisor_prueba(cuit="20000000002", razon_social="Jimena S.R.L.")
        Destinatario.objects.filter(pk=self.rodrigo.pk).update(
            facturacion_activa=True, emisor=self.emisor_a
        )
        Destinatario.objects.filter(pk=self.jimena.pk).update(
            facturacion_activa=True, emisor=self.emisor_b
        )

    def facturado(self, fecha, numero=42):
        cobro = self.cobro(fecha)
        factura = factura_prueba(
            cobro,
            emisor=self.emisor_a,
            estado="emitido",
            cbte_tipo=6,
            punto_venta=3,
            numero=numero,
            fecha=date(2026, 10, 20),
            neto="82.64",
            iva="17.36",
            total="100.00",
            cae=CAE,
            cae_vence=date(2026, 10, 30),
        )
        return cobro, factura

    def test_cambiar_el_destinatario_de_un_cobro_facturado_marca_a_revisar_sin_preguntar(self):
        cobro, factura = self.facturado(h(11, 30))
        _, otra = self.facturado(h(10, 30), numero=41)
        self.assertEqual(corregir_cambio(self.a, h(11), usuario=None), 1)
        factura.refresh_from_db()
        otra.refresh_from_db()
        cobro.refresh_from_db()
        self.assertEqual(cobro.destinatario, self.jimena)
        self.assertTrue(factura.a_revisar)
        self.assertTrue(factura.vigente)
        self.assertFalse(otra.a_revisar)  # su cobro no cambió de destinatario
        self.assertEqual(ComprobanteFiscal.objects.filter(tipo="nota_credito").count(), 0)
        evento = historial_de(factura).get(tipo=Evento.Tipo.CAMBIO)
        self.assertEqual(evento.origen, "corrección de sesión")
        self.assertIsNone(evento.usuario)
        self.assertEqual([c["campo"] for c in evento.cambios], ["a_revisar"])

    def facturas(self, cobro):
        return ComprobanteFiscal.objects.filter(cobro=cobro, tipo="factura", vigente=True)

    def test_un_cobro_sin_factura_que_pasa_a_un_destinatario_con_facturacion_queda_con_una(self):
        Destinatario.objects.filter(pk=self.rodrigo.pk).update(facturacion_activa=False)
        cobro = self.cobro(h(11, 30))
        self.assertFalse(ComprobanteFiscal.objects.exists())
        corregir_cambio(self.a, h(11), usuario=None)
        (factura,) = self.facturas(cobro)
        self.assertEqual((factura.estado, factura.emisor), ("pendiente", self.emisor_b))
        self.assertFalse(factura.a_revisar)

    def test_un_cobro_ya_facturado_no_recibe_una_segunda_factura(self):
        cobro, factura = self.facturado(h(11, 30))
        corregir_cambio(self.a, h(11), usuario=None)
        self.assertEqual(list(self.facturas(cobro)), [factura])
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)

    def test_una_factura_pendiente_pasa_al_emisor_nuevo_sin_duplicarse(self):
        cobro = self.cobro(h(11, 30))
        pendiente = factura_prueba(cobro, emisor=self.emisor_a)
        corregir_cambio(self.a, h(11), usuario=None)
        pendiente.refresh_from_db()
        self.assertEqual(list(self.facturas(cobro)), [pendiente])
        self.assertEqual(pendiente.emisor, self.emisor_b)
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)

    def test_no_factura_si_el_destinatario_nuevo_no_tiene_facturacion(self):
        Destinatario.objects.filter(pk=self.rodrigo.pk).update(facturacion_activa=False)
        Destinatario.objects.filter(pk=self.jimena.pk).update(facturacion_activa=False)
        self.cobro(h(11, 30))
        corregir_cambio(self.a, h(11), usuario=None)
        self.assertFalse(ComprobanteFiscal.objects.exists())

    def test_no_factura_un_cobro_cancelado_ni_uno_migrado_ni_uno_que_no_cambia_de_destinatario(
        self,
    ):
        Destinatario.objects.filter(pk=self.rodrigo.pk).update(facturacion_activa=False)
        self.cobro(h(11, 30), estado="cancelado")
        migrado = self.cobro(h(11, 40))
        Cobro.objects.filter(pk=migrado.pk).update(migrado=True)
        self.cobro(h(10, 30))  # sigue en A: no cambia de destinatario
        self.assertEqual(corregir_cambio(self.a, h(11), usuario=None), 2)
        self.assertFalse(ComprobanteFiscal.objects.exists())

    def test_el_administrador_emite_la_nota_y_la_factura_nueva_sale_con_el_emisor_nuevo(self):
        cobro, factura = self.facturado(h(11, 30))
        corregir_cambio(self.a, h(11), usuario=None)
        cobro = Cobro.objects.get(pk=cobro.pk)
        arca.resolver_a_revisar(cobro, usuario=self.admin, emitir=True)
        factura.refresh_from_db()
        (nota,) = ComprobanteFiscal.objects.filter(tipo="nota_credito")
        nueva = ComprobanteFiscal.objects.get(tipo="factura", vigente=True)
        self.assertFalse(factura.a_revisar)
        self.assertEqual(nota.emisor, self.emisor_a)
        self.assertEqual(nueva.emisor, self.emisor_b)

    def test_el_administrador_deja_la_factura_y_la_marca_se_quita(self):
        cobro, factura = self.facturado(h(11, 30))
        corregir_cambio(self.a, h(11), usuario=None)
        arca.resolver_a_revisar(Cobro.objects.get(pk=cobro.pk), usuario=self.admin, emitir=False)
        factura.refresh_from_db()
        self.assertFalse(factura.a_revisar)
        self.assertTrue(factura.vigente)
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)


# --- pantallas ---


class PantallasTest(Base):
    def setUp(self):
        super().setUp()
        self.operador = get_user_model().objects.create_user(
            "op", email="op2@example.com", role="operator"
        )

    def detalle(self, cobro):
        return self.client.get(reverse("cobros:detalle", args=[cobro.pk]))

    def test_cancelar_con_si_crea_la_nota_de_credito(self):
        cobro, _ = self.facturado()
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("cobros:cancelar", args=[cobro.pk]),
            {"motivo": "otro_medio", "estado_visto": "finalizado", "nota_credito": "si"},
        )
        self.assertRedirects(r, reverse("cobros:detalle", args=[cobro.pk]))
        self.assertEqual(self.notas().count(), 1)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "cancelado")

    def test_cancelar_sin_respuesta_repite_la_pregunta_y_no_cancela(self):
        cobro, _ = self.facturado()
        self.client.force_login(self.admin)
        for valor in ({}, {"nota_credito": "quizás"}):
            with self.subTest(valor=valor):
                r = self.client.post(
                    reverse("cobros:cancelar", args=[cobro.pk]),
                    {"motivo": "otro_medio", "estado_visto": "finalizado"} | valor,
                    follow=True,
                )
                self.assertContains(r, PREGUNTA_CANCELAR, status_code=400)
                self.assertContains(r, "Elegí Sí o No.", status_code=400)
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, "finalizado")

    def test_editar_el_monto_sin_elegir_nota_de_credito_muestra_la_pregunta_y_no_guarda(self):
        cobro, factura = self.facturado()
        self.client.force_login(self.admin)
        datos = {
            "fecha_0": localtime(cobro.fecha, ARG).strftime("%d/%m/%Y"),
            "fecha_1": localtime(cobro.fecha, ARG).strftime("%H:%M"),
            "monto": "70000,00",
            "metodo": cobro.metodo,
            "estado_visto": "finalizado",
        }
        for item in cobro.items.all():
            nuevo = D("45000.00") if item.numero == 1 else item.monto
            datos[f"monto_item_{item.pk}"] = str(nuevo).replace(".", ",")
        for extra in ({}, {"nota_credito": "quizás"}):
            with self.subTest(extra=extra):
                r = self.client.post(reverse("cobros:editar", args=[cobro.pk]), datos | extra)
                self.assertEqual(r.status_code, 200)  # sin redirección: el form vuelve con el error
                self.assertContains(r, PREGUNTA_EDITAR)
                cobro.refresh_from_db()
                self.assertEqual(cobro.monto, D("65000.00"))
                self.assertEqual(cobro.items.get(numero=1).monto, D("40000.00"))
                self.assertEqual(ComprobanteFiscal.objects.count(), 1)
                factura.refresh_from_db()
                self.assertTrue(factura.vigente)

    def test_el_operador_no_ve_los_botones_ni_la_seccion(self):
        cobro, factura = self.facturado()
        Cobro.objects.filter(pk=cobro.pk).update(creado_por=self.operador)
        self.client.force_login(self.operador)
        r = self.detalle(cobro)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "nota-credito")
        self.assertNotContains(r, reverse("arca:revisar", args=[cobro.pk]))
        self.assertNotContains(r, "Emitir nota de crédito")

    def test_el_boton_de_nota_de_credito_funciona(self):
        cobro, factura = self.facturado()
        self.client.force_login(self.admin)
        url = reverse("arca:nota_credito", args=[cobro.pk])
        r = self.client.post(url, follow=True)
        self.assertContains(r, "Nota de crédito pedida.")
        self.assertEqual(self.notas().count(), 1)
        r = self.client.post(url, follow=True)  # segunda vez: no hay factura emitida vigente
        self.assertContains(r, "El cobro no tiene una factura emitida vigente.")
        self.assertEqual(self.notas().count(), 1)

    def test_el_boton_de_revisar_funciona_con_si_y_con_no(self):
        cobro, factura = self.facturado()
        self.client.force_login(self.admin)
        url = reverse("arca:revisar", args=[cobro.pk])
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(a_revisar=True)
        r = self.client.post(url, {"emitir": "no"}, follow=True)
        self.assertContains(r, "Factura revisada.")
        factura.refresh_from_db()
        self.assertFalse(factura.a_revisar)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(a_revisar=True)
        r = self.client.post(url, {"emitir": "si"}, follow=True)
        self.assertContains(r, "Factura revisada.")
        self.assertEqual(self.notas().count(), 1)

    def test_revisar_sin_respuesta_valida_da_400_y_no_cambia_nada(self):
        cobro, factura = self.facturado()
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(a_revisar=True)
        self.client.force_login(self.admin)
        url = reverse("arca:revisar", args=[cobro.pk])
        for datos in ({}, {"emitir": "tal vez"}):
            self.assertEqual(self.client.post(url, datos).status_code, 400)
        self.assertEqual(self.notas().count(), 0)

    def test_operador_profesional_y_anonimo_no_pueden_usar_las_acciones(self):
        cobro, factura = self.facturado()
        Cobro.objects.filter(pk=cobro.pk).update(creado_por=self.operador)
        ComprobanteFiscal.objects.filter(pk=factura.pk).update(a_revisar=True)
        profesional = get_user_model().objects.create_user(
            "pro", email="pro@example.com", role="professional"
        )
        urls = (
            reverse("arca:nota_credito", args=[cobro.pk]),
            reverse("arca:revisar", args=[cobro.pk]),
        )
        for usuario in (self.operador, profesional):
            self.client.force_login(usuario)
            for url in urls:
                with self.subTest(rol=usuario.role, url=url):
                    self.assertEqual(self.client.post(url, {"emitir": "si"}).status_code, 403)
        self.client.logout()
        for url in urls:
            r = self.client.post(url, {"emitir": "si"})
            self.assertEqual(r.status_code, 302)
            self.assertIn(reverse("usuarios:login"), r["Location"])
        self.assertEqual(self.notas().count(), 0)

    def test_las_acciones_solo_aceptan_post(self):
        cobro, _ = self.facturado()
        self.client.force_login(self.admin)
        for nombre in ("arca:nota_credito", "arca:revisar"):
            self.assertEqual(self.client.get(reverse(nombre, args=[cobro.pk])).status_code, 405)

    def test_acciones_sobre_un_cobro_inexistente_dan_404(self):
        self.client.force_login(self.admin)
        for nombre in ("arca:nota_credito", "arca:revisar"):
            r = self.client.post(reverse(nombre, args=[999999]), {"emitir": "no"})
            self.assertEqual(r.status_code, 404)
