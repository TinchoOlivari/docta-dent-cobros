from unittest.mock import patch

from django.core.exceptions import ValidationError

from sistema.auditoria.servicios import historial_de
from sistema.cobros.models import ItemCobro
from sistema.dentalink import servicios
from sistema.dentalink.cliente import Resultado
from sistema.dentalink.tests import cobro_con_items
from sistema.dentalink.tests.test_envio import AHORA, CREAR, EnvioBase

E = ItemCobro.EstadoEnvio


def item_en(estado, **campos):
    item = cobro_con_items(["1000.00"]).items.get()
    ItemCobro.objects.filter(pk=item.pk).update(estado_envio=estado, **campos)
    item.refresh_from_db()
    return item


class AccionesTest(EnvioBase):
    def test_reintentar_error_envia_y_queda_enviado(self):
        item = item_en(E.ERROR, intentos=3, ultimo_error="x")
        with patch(CREAR, return_value=Resultado("ok", id_pago=1)):
            with self.captureOnCommitCallbacks(execute=True):
                servicios.reintentar(item, usuario=None)
        item.refresh_from_db()
        self.assertEqual((item.estado_envio, item.intentos, item.ultimo_error), ("enviado", 1, ""))

    def test_reintentar_revisar_hace_un_nuevo_intento(self):
        item = item_en(E.REVISAR, intentos=2)
        with patch(CREAR, return_value=Resultado("reintentable", error="caído")):
            with self.captureOnCommitCallbacks(execute=True):
                servicios.reintentar(item, usuario=None)
        item.refresh_from_db()
        self.assertEqual((item.estado_envio, item.intentos), ("pendiente", 1))

    def test_marcar_enviado_desde_error_deja_detalle(self):
        item = item_en(E.ERROR)
        servicios.marcar_enviado(item, usuario=None)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "enviado")
        self.assertEqual(historial_de(item)[0].detalle, "Cargado a mano en Dentalink")

    def test_marcar_enviado_desde_revisar_deja_detalle(self):
        item = item_en(E.REVISAR)
        servicios.marcar_enviado(item, usuario=None)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "enviado")
        self.assertEqual(historial_de(item)[0].detalle, "Verificado en Dentalink")

    def test_estados_no_permitidos_fallan(self):
        for estado in (E.ENVIADO, E.PENDIENTE, E.AJUSTADO):
            item = item_en(estado)
            for accion in (servicios.reintentar, servicios.marcar_enviado):
                with self.assertRaises(ValidationError):
                    accion(item, usuario=None)
        with self.assertRaises(ValidationError):
            servicios.marcar_ajustado(item_en(E.ENVIADO), usuario=None)
        with self.assertRaises(ValidationError):
            servicios.marcar_ajustado(item_en(E.ERROR), usuario=None)

    def test_marcar_ajustado_desde_requiere_ajuste(self):
        item = item_en(E.REQUIERE_AJUSTE)
        servicios.marcar_ajustado(item, usuario=None)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "ajustado")


class CambioDeCobroTest(EnvioBase):
    def estados(self, cobro):
        return [i.estado_envio for i in cobro.items.order_by("numero")]

    def test_edicion_pasa_enviados_a_requiere_ajuste(self):
        cobro = cobro_con_items(["1000.00", "2000.00"])
        cobro.items.update(estado_envio=E.ENVIADO)
        servicios.al_cambiar_cobro(cobro, usuario=None, cancelado=False)
        self.assertEqual(self.estados(cobro), ["requiere_ajuste"] * 2)
        for i in cobro.items.all():
            self.assertEqual(len(historial_de(i)), 1)

    def test_cancelacion_por_estado(self):
        cobro = cobro_con_items(["1000.00"] * 5)
        orden = [E.ENVIADO, E.REVISAR, E.PENDIENTE, E.ERROR, E.HISTORICO]
        for n, estado in enumerate(orden, start=1):
            cobro.items.filter(numero=n).update(estado_envio=estado)
        servicios.al_cambiar_cobro(cobro, usuario=None, cancelado=True)
        self.assertEqual(
            self.estados(cobro),
            ["requiere_ajuste", "requiere_ajuste", "cancelado", "cancelado", "historico"],
        )

    def test_edicion_no_toca_pendiente_ni_error_ni_revisar(self):
        cobro = cobro_con_items(["1000.00"] * 3)
        for n, estado in enumerate([E.PENDIENTE, E.ERROR, E.REVISAR], start=1):
            cobro.items.filter(numero=n).update(estado_envio=estado)
        servicios.al_cambiar_cobro(cobro, usuario=None, cancelado=False)
        self.assertEqual(self.estados(cobro), ["pendiente", "error", "revisar"])
        for i in cobro.items.all():
            self.assertEqual(len(historial_de(i)), 0)


class EnvioEnCursoTest(EnvioBase):
    def enviar_con(self, resultado, *, cancelado):
        cobro = cobro_con_items(["1000.00"])
        item = cobro.items.get()

        def durante(datos):
            servicios.al_cambiar_cobro(cobro, usuario=None, cancelado=cancelado)
            return resultado

        with patch(CREAR, side_effect=durante):
            servicios.enviar_item(item.pk, ahora=AHORA)
        item.refresh_from_db()
        return item

    def test_cancelacion_durante_envio_deja_requiere_ajuste_con_id(self):
        item = self.enviar_con(Resultado("ok", id_pago=7001), cancelado=True)
        self.assertEqual(item.estado_envio, "requiere_ajuste")
        self.assertEqual(item.id_pago_dentalink, 7001)
        self.assertIsNone(item.en_curso_desde)

    def test_edicion_durante_envio_deja_requiere_ajuste_con_id(self):
        item = self.enviar_con(Resultado("ok", id_pago=7002), cancelado=False)
        self.assertEqual(item.estado_envio, "requiere_ajuste")
        self.assertEqual(item.id_pago_dentalink, 7002)

    def test_rechazado_durante_cancelacion_sigue_requiere_ajuste(self):
        item = self.enviar_con(Resultado("rechazado", error="no"), cancelado=True)
        self.assertEqual(item.estado_envio, "requiere_ajuste")
        self.assertIsNone(item.id_pago_dentalink)

    def test_un_evento_de_envio_por_intento(self):
        item = self.enviar_con(Resultado("ok", id_pago=7003), cancelado=True)
        tipos = [e.tipo for e in historial_de(item)]
        self.assertEqual(tipos.count("envio_dentalink"), 1)
