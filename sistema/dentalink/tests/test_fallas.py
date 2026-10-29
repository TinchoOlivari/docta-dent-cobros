from datetime import datetime, timedelta
from unittest.mock import patch

from django.utils import timezone

from sistema.auditoria.servicios import historial_de
from sistema.cobros.models import ItemCobro
from sistema.core.tiempo import ARG
from sistema.dentalink import servicios, tasks
from sistema.dentalink.cliente import Resultado
from sistema.dentalink.tests import cobro_con_items
from sistema.dentalink.tests.test_envio import CREAR, EnvioBase

AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=ARG)
AVISAR = "sistema.dentalink.servicios.avisar_falla"
CAIDO = Resultado("reintentable", error="caído")


class FallasTest(EnvioBase):
    def enviar(self, item, resultados, ahora=AHORA):
        with patch(CREAR, side_effect=resultados) as crear:
            return servicios.enviar_item(item.pk, ahora=ahora), crear

    def test_503_reprograma_y_antes_de_la_hora_se_omite(self):
        item = cobro_con_items(["1000.00"]).items.get()
        self.enviar(item, [CAIDO])
        item.refresh_from_db()
        self.assertEqual((item.estado_envio, item.intentos), ("pendiente", 1))
        self.assertEqual(item.proximo_intento, AHORA + timedelta(minutes=1))
        antes = AHORA + timedelta(minutes=1) - timedelta(seconds=1)
        estado, crear = self.enviar(item, [CAIDO], ahora=antes)
        self.assertEqual(estado, "omitido")
        crear.assert_not_called()

    def test_esperas_1_2_4_8_16_en_los_intentos_1_a_5(self):
        item = cobro_con_items(["1000.00"]).items.get()
        ahora = AHORA
        for n, espera in enumerate([1, 2, 4, 8, 16], start=1):
            self.enviar(item, [CAIDO], ahora=ahora)
            item.refresh_from_db()
            self.assertEqual(item.intentos, n)
            self.assertEqual(item.proximo_intento, ahora + timedelta(minutes=espera))
            ahora = item.proximo_intento
        self.assertEqual(servicios.ESPERAS, [1, 2, 4, 8, 16])
        self.assertLess(sum(servicios.ESPERAS), 60)

    def test_cinco_fallas_y_la_sexta_ok_queda_enviado(self):
        item = cobro_con_items(["1000.00"]).items.get()
        ahora = AHORA
        with patch(CREAR, side_effect=[CAIDO] * 5 + [Resultado("ok", id_pago=1)]) as crear:
            for _ in range(6):
                servicios.enviar_item(item.pk, ahora=ahora)
                ahora += timedelta(hours=1)
        item.refresh_from_db()
        self.assertEqual((item.estado_envio, item.intentos), ("enviado", 6))
        self.assertEqual(crear.call_count, 6)

    def test_seis_fallas_dan_error_y_nunca_hay_septimo_intento(self):
        item = cobro_con_items(["1000.00"]).items.get()
        ahora = AHORA
        with patch(CREAR, side_effect=[CAIDO] * 7) as crear, patch(AVISAR) as aviso:
            for _ in range(7):
                servicios.enviar_item(item.pk, ahora=ahora)
                ahora += timedelta(hours=1)
        item.refresh_from_db()
        self.assertEqual((item.estado_envio, item.intentos), ("error", 6))
        self.assertEqual(crear.call_count, 6)
        aviso.assert_called_once()

    def test_dos_fallas_y_ok_deja_tres_eventos(self):
        item = cobro_con_items(["1000.00"]).items.get()
        ahora = AHORA
        with patch(CREAR, side_effect=[CAIDO, CAIDO, Resultado("ok", id_pago=5)]):
            for _ in range(3):
                servicios.enviar_item(item.pk, ahora=ahora)
                ahora += timedelta(hours=1)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "enviado")
        eventos = list(historial_de(item).filter(tipo="envio_dentalink").order_by("pk"))
        self.assertEqual([e.exito for e in eventos], [False, False, True])
        for n, e in enumerate(eventos, start=1):
            self.assertIn(f"Intento {n}", e.detalle)

    def test_rechazado_ya_pagada_da_error_al_primer_intento(self):
        item = cobro_con_items(["1000.00"]).items.get()
        with patch(AVISAR) as aviso:
            estado, crear = self.enviar(item, [Resultado("rechazado", error="ya pagada")])
        item.refresh_from_db()
        self.assertEqual((estado, item.intentos), ("error", 1))
        self.assertIn("ya pagada", item.ultimo_error)
        self.assertEqual(crear.call_count, 1)
        aviso.assert_called_once()

    def test_dudoso_y_sin_id_van_a_revisar_con_una_llamada(self):
        for tipo in ("dudoso", "sin_id"):
            with self.subTest(tipo=tipo):
                item = cobro_con_items(["1000.00"]).items.get()
                with patch(AVISAR) as aviso:
                    estado, crear = self.enviar(item, [Resultado(tipo, error="x")])
                self.assertEqual(estado, "revisar")
                self.assertEqual(crear.call_count, 1)
                aviso.assert_called_once()

    def test_enviado_no_avisa(self):
        item = cobro_con_items(["1000.00"]).items.get()
        with patch(AVISAR) as aviso:
            self.enviar(item, [Resultado("ok", id_pago=1)])
        aviso.assert_not_called()


class ColgadosTest(EnvioBase):
    def colgado(self, hace):
        cobro = cobro_con_items(["1000.00"])
        ItemCobro.objects.filter(cobro=cobro).update(en_curso_desde=AHORA - hace)
        return cobro.items.get()

    def test_hace_10_min_1_s_pasa_a_revisar_y_avisa(self):
        item = self.colgado(timedelta(minutes=10, seconds=1))
        with patch(AVISAR) as aviso:
            self.assertEqual(servicios.liberar_colgados(ahora=AHORA), 1)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "revisar")
        self.assertIsNone(item.en_curso_desde)
        self.assertEqual(item.ultimo_error, servicios.ERROR_COLGADO)
        aviso.assert_called_once()
        evento = historial_de(item).get(tipo="envio_dentalink")
        self.assertFalse(evento.exito)

    def test_hace_10_min_justos_sigue_pendiente(self):
        item = self.colgado(timedelta(minutes=10))
        self.assertEqual(servicios.liberar_colgados(ahora=AHORA), 0)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "pendiente")

    def test_hace_9_min_59_s_sigue_pendiente(self):
        item = self.colgado(timedelta(minutes=9, seconds=59))
        with patch(AVISAR) as aviso:
            self.assertEqual(servicios.liberar_colgados(ahora=AHORA), 0)
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "pendiente")
        aviso.assert_not_called()

    def test_item_sin_en_curso_no_se_toca(self):
        cobro_con_items(["1000.00"])
        self.assertEqual(servicios.liberar_colgados(ahora=AHORA), 0)

    def test_enviar_item_con_en_curso_se_omite_sin_llamar(self):
        item = self.colgado(timedelta(minutes=1))
        with patch(CREAR) as crear:
            self.assertEqual(servicios.enviar_item(item.pk, ahora=AHORA), "omitido")
        crear.assert_not_called()


class TareasTest(EnvioBase):
    def test_enviar_pendientes_envia_vencidos_y_no_toca_futuros_ni_cobro_pendiente(self):
        vencido = cobro_con_items(["1000.00"]).items.get()
        futuro = cobro_con_items(["1000.00"]).items.get()
        de_pendiente = cobro_con_items(["1000.00"], estado="pendiente").items.get()
        ItemCobro.objects.filter(pk=vencido.pk).update(
            proximo_intento=timezone.now() - timedelta(hours=1)
        )
        ItemCobro.objects.filter(pk=futuro.pk).update(
            proximo_intento=timezone.now() + timedelta(days=3650)
        )
        with patch(CREAR, side_effect=self.respuesta) as crear:
            tasks.enviar_pendientes()
        self.assertEqual(crear.call_count, 1)
        estados = {i.pk: i.estado_envio for i in ItemCobro.objects.all()}
        self.assertEqual(estados[vencido.pk], "enviado")
        self.assertEqual(estados[futuro.pk], "pendiente")
        self.assertEqual(estados[de_pendiente.pk], "pendiente")

    def test_enviar_pendientes_libera_colgados_primero(self):
        item = cobro_con_items(["1000.00"]).items.get()
        ItemCobro.objects.update(en_curso_desde=timezone.now() - timedelta(days=1))
        with patch(CREAR) as crear, patch(AVISAR):
            tasks.enviar_pendientes()
        crear.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.estado_envio, "revisar")

    def test_enviar_pendientes_marca_en_curso_con_la_hora_de_cada_envio(self):
        cobro_con_items(["1000.00"])
        cobro_con_items(["1000.00"])
        reloj, marcas = [AHORA], []

        def crear(datos):
            marcas.append(ItemCobro.objects.get(en_curso_desde__isnull=False).en_curso_desde)
            reloj[0] += timedelta(minutes=11)
            return Resultado("ok", id_pago=len(marcas))

        with (
            patch("django.utils.timezone.now", side_effect=lambda: reloj[0]),
            patch(CREAR, side_effect=crear),
        ):
            tasks.enviar_pendientes()
        self.assertEqual(marcas, [AHORA, AHORA + timedelta(minutes=11)])

    def test_enviar_cobro_envia_los_items_en_orden_de_numero(self):
        cobro = cobro_con_items(["1000.00", "2000.00"])
        with patch(CREAR, side_effect=self.respuesta) as crear:
            tasks.enviar_cobro(cobro.pk)
        montos = [c.args[0]["monto_pago"] for c in crear.call_args_list]
        self.assertEqual(montos, ["1000.00", "2000.00"])

    def test_encolar_envio_llama_delay_solo_si_esta_listo(self):
        listo = cobro_con_items(["1000.00"])
        no_listo = cobro_con_items(["500.00"], monto="1000.00")
        with patch("sistema.dentalink.tasks.enviar_cobro.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                servicios.encolar_envio(listo)
                servicios.encolar_envio(no_listo)
        delay.assert_called_once_with(listo.pk)
