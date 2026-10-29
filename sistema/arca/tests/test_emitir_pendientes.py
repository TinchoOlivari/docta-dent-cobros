from datetime import date, timedelta
from unittest import mock

from sistema.arca import tasks
from sistema.arca.tests.fabricas import AHORA, cobro_facturable, factura_prueba
from sistema.core.tests import SistemaTestCase

EMITIR = "sistema.arca.servicios.emitir"


class EmitirPendientesTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)

    def factura(self, **campos):
        return factura_prueba(cobro_facturable(), **campos)

    def correr(self):
        with mock.patch(EMITIR) as emitir:
            tasks.emitir_pendientes()
        return [c.args[0] for c in emitir.call_args_list]

    def test_pendiente_sin_proximo_intento_se_emite(self):
        factura = self.factura()
        self.assertEqual(self.correr(), [factura.pk])

    def test_proximo_intento_justo_ahora_se_emite(self):
        factura = self.factura(proximo_intento=AHORA)
        self.assertEqual(self.correr(), [factura.pk])

    def test_proximo_intento_un_segundo_antes_se_emite(self):
        factura = self.factura(proximo_intento=AHORA - timedelta(seconds=1))
        self.assertEqual(self.correr(), [factura.pk])

    def test_proximo_intento_un_segundo_despues_no_se_emite(self):
        self.factura(proximo_intento=AHORA + timedelta(seconds=1))
        self.assertEqual(self.correr(), [])

    def test_en_curso_no_se_emite(self):
        self.factura(en_curso_desde=AHORA)
        self.assertEqual(self.correr(), [])

    def test_colgado_de_mas_de_10_minutos_se_libera_y_se_emite_en_la_misma_corrida(self):
        colgado = self.factura(en_curso_desde=AHORA - timedelta(minutes=10, seconds=1))
        self.assertEqual(self.correr(), [colgado.pk])

    def test_en_curso_de_justo_10_minutos_sigue_en_curso(self):
        self.factura(en_curso_desde=AHORA - timedelta(minutes=10))
        self.assertEqual(self.correr(), [])

    def test_si_liberar_colgados_falla_los_pendientes_igual_se_emiten(self):
        factura = self.factura()
        with (
            mock.patch("sistema.arca.servicios.liberar_colgados", side_effect=RuntimeError),
            self.assertLogs("sistema.arca.tasks", level="ERROR"),
            mock.patch(EMITIR) as emitir,
        ):
            tasks.emitir_pendientes()
        emitir.assert_called_once_with(factura.pk)

    def test_error_y_descartado_no_se_emiten(self):
        self.factura(estado="error", motivo="certificado vencido")
        self.factura(estado="descartado")
        self.assertEqual(self.correr(), [])

    def test_emitido_no_se_emite(self):
        self.factura(
            estado="emitido",
            numero=1,
            cae="70000000000001",
            cae_vence=date(2026, 10, 30),
            fecha=date(2026, 10, 20),
            neto="100.00",
            iva="0.00",
            total="100.00",
        )
        self.assertEqual(self.correr(), [])

    def test_emite_en_orden_de_pk_solo_los_vencidos(self):
        a = self.factura()
        self.factura(proximo_intento=AHORA + timedelta(minutes=1))
        c = self.factura(proximo_intento=AHORA - timedelta(minutes=1))
        self.assertEqual(self.correr(), [a.pk, c.pk])

    def test_si_un_comprobante_falla_los_demas_se_emiten(self):
        a, b = self.factura(), self.factura()
        with (
            mock.patch(EMITIR, side_effect=[RuntimeError, "emitido"]) as emitir,
            self.assertLogs("sistema.arca.tasks", level="ERROR") as logs,
        ):
            tasks.emitir_pendientes()
        self.assertEqual([c.args[0] for c in emitir.call_args_list], [a.pk, b.pk])
        self.assertIn(f"comprobante={a.pk}", logs.output[0])

    def test_la_tarea_emitir_llama_al_servicio_con_el_id(self):
        with mock.patch(EMITIR, return_value="omitido") as emitir:
            self.assertEqual(tasks.emitir(7), "omitido")
        emitir.assert_called_once_with(7)
