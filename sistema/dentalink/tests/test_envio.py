from datetime import datetime, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from sistema.auditoria.models import Evento
from sistema.cobros.models import ItemCobro
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.dentalink import servicios
from sistema.dentalink.cliente import Resultado
from sistema.dentalink.models import Banco, ConfiguracionDentalink, MedioPago
from sistema.dentalink.tests import cobro_con_items, copia_basica, cuenta_transferencia

CREAR = "sistema.dentalink.servicios.cliente.crear_pago"
AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=ARG)


class EnvioBase(SistemaTestCase):
    def setUp(self):
        self.otro = copia_basica()
        self.n = 0

    def respuesta(self, *args, **kwargs):
        self.n += 1
        return Resultado("ok", id_pago=7000 + self.n)

    def enviar_todo(self, cobro):
        with patch(CREAR, side_effect=self.respuesta) as crear:
            for item in cobro.items.order_by("numero"):
                servicios.enviar_item(item.pk, ahora=AHORA)
        return crear


class EnvioTest(EnvioBase):
    def test_cobro_finalizado_manda_un_pago_por_item(self):
        cobro = cobro_con_items(["40000.00", "25000.00"])
        crear = self.enviar_todo(cobro)
        self.assertEqual(crear.call_count, 2)
        datos = [c.args[0] for c in crear.call_args_list]
        self.assertEqual(
            [d["numero_referencia"] for d in datos], [f"{cobro.pk}-1", f"{cobro.pk}-2"]
        )
        self.assertEqual([d["monto_pago"] for d in datos], ["40000.00", "25000.00"])
        self.assertEqual([d["id_banco"] for d in datos], [20, 20])
        self.assertEqual(datos[0]["abonos"], [{"id_detalle": 9001, "monto_abono": "40000.00"}])
        items = list(cobro.items.order_by("numero"))
        self.assertEqual([i.estado_envio for i in items], ["enviado", "enviado"])
        self.assertEqual([i.id_pago_dentalink for i in items], [7001, 7002])

    def test_evento_guarda_estado_anterior_y_nuevo(self):
        cobro = cobro_con_items(["1000.00"])
        self.enviar_todo(cobro)
        evento = Evento.objects.get(tipo=Evento.Tipo.ENVIO_DENTALINK)
        campos = {c["campo"]: (c["anterior"], c["nuevo"]) for c in evento.cambios}
        self.assertEqual(campos["estado_envio"], ("pendiente", "enviado"))
        self.assertEqual(campos["intentos"], (0, 1))
        self.assertEqual(campos["id_pago_dentalink"], (None, 7001))
        self.assertTrue(evento.exito)

    def test_cuenta_sin_banco_usa_banco_por_defecto(self):
        ConfiguracionDentalink.actual()
        ConfiguracionDentalink.objects.update(banco_por_defecto=self.otro)
        cobro = cobro_con_items(["1000.00"], cuenta=cuenta_transferencia(banco=""))
        crear = self.enviar_todo(cobro)
        self.assertEqual(crear.call_args.args[0]["id_banco"], 99)

    def test_cuenta_sin_banco_y_sin_default_da_error_sin_llamar(self):
        cobro = cobro_con_items(["1000.00"], cuenta=cuenta_transferencia(banco=""))
        crear = self.enviar_todo(cobro)
        self.assertEqual(crear.call_count, 0)
        item = cobro.items.get()
        self.assertEqual(item.estado_envio, "error")
        self.assertIn("no tiene banco", item.ultimo_error)

    def test_debito_con_banco_emisor_manda_id_banco_del_emisor(self):
        cobro = cobro_con_items(["1000.00"], metodo="debito")
        cobro.banco_emisor = Banco.objects.get(id_dentalink=99)
        cobro.save()
        crear = self.enviar_todo(cobro)
        datos = crear.call_args.args[0]
        self.assertEqual(datos["id_banco"], 99)
        self.assertNotIn("numero_referencia", datos)

    def test_reintentable_programa_el_proximo_intento(self):
        cobro = cobro_con_items(["1000.00"])
        with patch(CREAR, return_value=Resultado("reintentable", error="caído")):
            estado = servicios.enviar_item(cobro.items.get().pk, ahora=AHORA)
        item = cobro.items.get()
        self.assertEqual(estado, "pendiente")
        self.assertEqual(item.intentos, 1)
        self.assertEqual(item.proximo_intento, AHORA + timedelta(minutes=1))
        self.assertIsNone(item.en_curso_desde)

    def test_item_con_proximo_intento_futuro_se_omite(self):
        cobro = cobro_con_items(["1000.00"])
        ItemCobro.objects.update(proximo_intento=AHORA + timedelta(seconds=1))
        with patch(CREAR) as crear:
            self.assertEqual(servicios.enviar_item(cobro.items.get().pk, ahora=AHORA), "omitido")
        crear.assert_not_called()

    def test_item_con_proximo_intento_justo_ahora_se_envia(self):
        cobro = cobro_con_items(["1000.00"])
        ItemCobro.objects.update(proximo_intento=AHORA)
        with patch(CREAR, side_effect=self.respuesta):
            self.assertEqual(servicios.enviar_item(cobro.items.get().pk, ahora=AHORA), "enviado")

    def test_item_en_curso_se_omite(self):
        cobro = cobro_con_items(["1000.00"])
        ItemCobro.objects.update(en_curso_desde=AHORA)
        with patch(CREAR) as crear:
            self.assertEqual(servicios.enviar_item(cobro.items.get().pk, ahora=AHORA), "omitido")
        crear.assert_not_called()

    def test_item_inexistente_se_omite(self):
        self.assertEqual(servicios.enviar_item(0, ahora=AHORA), "omitido")


class CobroListoTest(EnvioBase):
    def listo(self, montos, **kw):
        return servicios.cobro_listo(cobro_con_items(montos, **kw))

    def test_items_menores_al_monto_no_esta_listo(self):
        self.assertFalse(self.listo(["65000.00"], monto="70000.00"))

    def test_items_suman_justo_el_monto_esta_listo(self):
        self.assertTrue(self.listo(["64999.99", "5000.01"], monto="70000.00"))

    def test_items_un_centavo_arriba_no_esta_listo(self):
        self.assertFalse(self.listo(["70000.01"], monto="70000.00"))

    def test_items_un_centavo_abajo_no_esta_listo(self):
        self.assertFalse(self.listo(["69999.99"], monto="70000.00"))

    def test_cero_items_no_esta_listo(self):
        self.assertFalse(self.listo([], monto="70000.00"))

    def test_faltan_items_con_suma_exacta_no_esta_listo(self):
        cobro = cobro_con_items(["1000.00"])
        cobro.faltan_items = True
        cobro.save()
        self.assertFalse(servicios.cobro_listo(cobro))

    def test_pendiente_y_cancelado_no_estan_listos(self):
        self.assertFalse(self.listo(["1000.00"], estado="pendiente"))
        self.assertFalse(self.listo(["1000.00"], estado="cancelado"))

    def test_enviar_item_de_cobro_no_listo_se_omite(self):
        cobro = cobro_con_items(["65000.00"], monto="70000.00")
        crear = self.enviar_todo(cobro)
        crear.assert_not_called()
        self.assertEqual(cobro.items.get().estado_envio, "pendiente")
        self.assertEqual(servicios.enviar_item(cobro.items.get().pk, ahora=AHORA), "omitido")

    def test_encolar_envio_solo_si_esta_listo(self):
        listo = cobro_con_items(["1000.00"])
        no_listo = cobro_con_items(["500.00"], monto="1000.00")
        with patch("sistema.dentalink.tasks.enviar_cobro.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True) as cbs:
                servicios.encolar_envio(no_listo)
                self.assertEqual(len(cbs), 0)
                servicios.encolar_envio(listo)
        delay.assert_called_once_with(listo.pk)


class MedioPagoTest(EnvioBase):
    def test_id_medio_pago_por_metodo(self):
        esperado = {
            "efectivo": 1,
            "transferencia": 2,
            "debito": 3,
            "credito": 4,
            "qr": 5,
            "link": 5,
        }
        for metodo, id_medio in esperado.items():
            with self.subTest(metodo=metodo):
                cobro = cobro_con_items(["1000.00"], metodo=metodo)
                crear = self.enviar_todo(cobro)
                self.assertEqual(crear.call_args.args[0]["id_medio_pago"], id_medio)

    def test_medio_no_vigente_da_error_sin_llamar(self):
        MedioPago.objects.filter(nombre="Contado").update(vigente=False)
        cobro = cobro_con_items(["1000.00"], metodo="qr")
        crear = self.enviar_todo(cobro)
        crear.assert_not_called()
        item = cobro.items.get()
        self.assertEqual(item.estado_envio, "error")
        self.assertIn("no está en la copia", item.ultimo_error)
        self.assertEqual(item.intentos, 0)

    def test_item_enviado_o_historico_se_omite(self):
        for estado in ("enviado", "historico"):
            with self.subTest(estado=estado):
                cobro = cobro_con_items(["1000.00"])
                ItemCobro.objects.filter(cobro=cobro).update(estado_envio=estado)
                crear = self.enviar_todo(cobro)
                crear.assert_not_called()
                self.assertEqual(servicios.enviar_item(cobro.items.get().pk), "omitido")

    def test_fecha_recepcion_es_la_fecha_argentina(self):
        cobro = cobro_con_items(["1000.00"])
        cobro.fecha = datetime(2026, 10, 31, 23, 30, tzinfo=ARG)
        cobro.save()
        crear = self.enviar_todo(cobro)
        self.assertEqual(crear.call_args.args[0]["fecha_recepcion"], "2026-10-31")

    def test_monto_se_manda_con_dos_decimales(self):
        cobro = cobro_con_items(["0.01"])
        crear = self.enviar_todo(cobro)
        self.assertEqual(crear.call_args.args[0]["monto_pago"], "0.01")
        self.assertEqual(D(crear.call_args.args[0]["abonos"][0]["monto_abono"]), D("0.01"))
