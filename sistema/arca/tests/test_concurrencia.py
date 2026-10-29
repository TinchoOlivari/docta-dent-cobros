import threading
from unittest import mock

from django.db import connection, transaction

from sistema.arca import servicios as arca
from sistema.arca.cliente import Resultado
from sistema.arca.models import ComprobanteFiscal
from sistema.arca.tests.fabricas import (
    AHORA,
    ArcaFalso,
    cobro_facturable,
    emisor_prueba,
    factura_prueba,
)
from sistema.core.tests import SistemaTransactionTestCase
from sistema.destinatarios.models import Cuenta, Destinatario


class DosFacturasALaVezTest(SistemaTransactionTestCase):
    def setUp(self):
        super().setUp()
        destinatario = Destinatario.objects.create(
            nombre="Clínica", tipo="clinica", facturacion_activa=True, emisor=emisor_prueba()
        )
        cuenta = Cuenta.objects.create(destinatario=destinatario, tipo="efectivo")
        self.cobro = cobro_facturable(cuenta=cuenta, destinatario=destinatario)

    def test_dos_hilos_sobre_el_mismo_cobro_dejan_una_sola_factura(self):
        adentro, soltar = threading.Event(), threading.Event()
        creadas = []
        alta = arca.registrar_alta

        def lento(*args, **kwargs):
            resultado = alta(*args, **kwargs)
            adentro.set()  # el hilo 1 ya tiene el cobro bloqueado y la factura creada
            soltar.wait(timeout=30)
            return resultado

        def llamar():
            try:
                with transaction.atomic():
                    creadas.append(arca.al_finalizar(self.cobro))
            finally:
                connection.close()

        with (
            mock.patch("sistema.arca.tasks.emitir.delay") as emitir,
            mock.patch.object(arca, "registrar_alta", lento),
        ):
            h1 = threading.Thread(target=llamar)
            h1.start()
            self.assertTrue(adentro.wait(timeout=30))
            h2 = threading.Thread(target=llamar)
            h2.start()
            h2.join(timeout=1)  # el hilo 2 espera el bloqueo del cobro
            self.assertTrue(h2.is_alive())
            soltar.set()
            h1.join(timeout=30)
            h2.join(timeout=30)
        self.assertEqual(ComprobanteFiscal.objects.count(), 1)
        self.assertEqual(sum(c is not None for c in creadas), 1)
        emitir.assert_called_once_with(ComprobanteFiscal.objects.get().pk)


ESPERA = 30  # segundos: tope para no colgar la suite si algo sale mal, nunca una pausa


class EmisionALaVezTest(SistemaTransactionTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        self.arca = ArcaFalso(self)
        self.emisor = emisor_prueba()
        self.resultados = {}

    def factura(self):
        return factura_prueba(cobro_facturable(dni_paciente="30111222"), emisor=self.emisor)

    def hilo(self, nombre, funcion):
        def correr():
            try:
                self.resultados[nombre] = funcion()
            except BaseException as e:  # el hilo principal lo vuelve a mostrar
                self.resultados[nombre] = e
            finally:
                connection.close()

        hilo = threading.Thread(target=correr)
        hilo.start()
        return hilo

    def terminar(self, *hilos):
        for hilo in hilos:
            hilo.join(timeout=ESPERA)
            self.assertFalse(hilo.is_alive())

    def bloquear_primera_llamada(self, nombre, adentro, soltar):
        """La primera llamada a `cliente.<nombre>` avisa que llegó y espera; las demás pasan."""
        original = getattr(self.arca, nombre).side_effect
        llamadas = []

        def lenta(*args):
            llamadas.append(args)
            if len(llamadas) == 1:
                adentro.set()
                soltar.wait(timeout=ESPERA)
            return original(*args)

        getattr(self.arca, nombre).side_effect = lenta

    def esperando_la_fila_del_emisor(self):
        """Hay una sesión esperando el advisory lock del emisor (consulta de condición, sin pausas)."""
        sin_pausa = threading.Event()
        for _ in range(ESPERA * 100):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_locks"
                    " WHERE locktype = 'advisory' AND NOT granted AND classid = %s",
                    [arca.LLAVE_ARCA],
                )
                if cursor.fetchone()[0]:
                    return True
            sin_pausa.wait(0.01)
        return False

    def test_dos_hilos_sobre_el_mismo_comprobante_emiten_una_sola_vez(self):
        factura = self.factura()
        adentro, soltar = threading.Event(), threading.Event()
        self.bloquear_primera_llamada("solicitar_cae", adentro, soltar)
        h1 = self.hilo("uno", lambda: arca.emitir(factura.pk))
        self.assertTrue(adentro.wait(timeout=ESPERA))  # el hilo 1 está en medio de la llamada
        h2 = self.hilo("dos", lambda: arca.emitir(factura.pk))
        self.terminar(h2)
        self.assertEqual(self.resultados["dos"], "omitido")
        soltar.set()
        self.terminar(h1)
        self.assertEqual(self.resultados["uno"], "emitido")
        self.assertEqual(self.arca.solicitar_cae.call_count, 1)
        self.assertEqual(ComprobanteFiscal.objects.get().numero, 42)

    def test_comprobante_tomado_por_otra_transaccion_se_omite_sin_esperar(self):
        factura = self.factura()
        tomado, soltar = threading.Event(), threading.Event()

        def tomar():
            with transaction.atomic():
                ComprobanteFiscal.objects.select_for_update().get(pk=factura.pk)
                tomado.set()
                soltar.wait(timeout=ESPERA)

        h1 = self.hilo("toma", tomar)
        self.assertTrue(tomado.wait(timeout=ESPERA))
        h2 = self.hilo("dos", lambda: arca.emitir(factura.pk))
        self.terminar(h2)
        self.assertEqual(self.resultados["dos"], "omitido")
        self.assertTrue(self.arca.no_llamo_a_arca())
        soltar.set()
        self.terminar(h1)
        self.assertEqual(ComprobanteFiscal.objects.get().estado, "pendiente")

    def test_dos_facturas_del_mismo_emisor_reciben_el_42_y_el_43_nunca_el_mismo(self):
        una, otra = self.factura(), self.factura()
        adentro, soltar = threading.Event(), threading.Event()
        # El hilo 1 tiene la fila del emisor y todavía no pidió número: el hilo 2 espera su turno.
        self.bloquear_primera_llamada("ultimo_autorizado", adentro, soltar)
        h1 = self.hilo("uno", lambda: arca.emitir(una.pk))
        self.assertTrue(adentro.wait(timeout=ESPERA))
        h2 = self.hilo("dos", lambda: arca.emitir(otra.pk))
        self.assertTrue(self.esperando_la_fila_del_emisor())
        soltar.set()
        self.terminar(h1, h2)
        self.assertEqual(self.resultados, {"uno": "emitido", "dos": "emitido"})
        numeros = sorted(c.numero for c in ComprobanteFiscal.objects.all())
        self.assertEqual(numeros, [42, 43])
        pedidos = [c.args[2]["numero"] for c in self.arca.solicitar_cae.call_args_list]
        self.assertEqual(sorted(pedidos), [42, 43])
        self.assertEqual(ComprobanteFiscal.objects.get(pk=una.pk).numero, 42)

    def test_un_dudoso_en_vuelo_hace_que_la_otra_emision_espere_su_turno(self):
        una, otra = self.factura(), self.factura()
        adentro, soltar = threading.Event(), threading.Event()
        self.bloquear_primera_llamada("solicitar_cae", adentro, soltar)
        h1 = self.hilo("uno", lambda: arca.emitir(una.pk))
        self.assertTrue(adentro.wait(timeout=ESPERA))  # el 42 salió y no hay respuesta
        h2 = self.hilo("dos", lambda: arca.emitir(otra.pk))
        self.terminar(h2)
        self.assertEqual(self.resultados["dos"], "omitido")  # no pide otro número
        self.assertEqual(self.arca.solicitar_cae.call_count, 1)
        soltar.set()
        self.terminar(h1)
        self.assertEqual(arca.emitir(otra.pk), "emitido")
        self.assertEqual(ComprobanteFiscal.objects.get(pk=otra.pk).numero, 43)

    def test_un_dudoso_que_sale_mientras_la_otra_espera_la_fila_la_hace_esperar(self):
        una, otra = self.factura(), self.factura()
        adentro, soltar = threading.Event(), threading.Event()
        # Los 2 pasan el paso 1 antes de que exista el dudoso; el 42 sale y no hay respuesta.
        self.bloquear_primera_llamada("ultimo_autorizado", adentro, soltar)
        self.arca.responder(Resultado("dudoso", error="timeout"))
        h1 = self.hilo("uno", lambda: arca.emitir(una.pk))
        self.assertTrue(adentro.wait(timeout=ESPERA))
        h2 = self.hilo("dos", lambda: arca.emitir(otra.pk))
        self.assertTrue(self.esperando_la_fila_del_emisor())
        soltar.set()
        self.terminar(h1, h2)
        self.assertEqual(self.resultados, {"uno": "pendiente", "dos": "pendiente"})
        # ARCA puede seguir procesando el 42: consultarlo ya daría "no existe" y saldría otro.
        self.assertFalse(self.arca.consultar.called)
        self.assertEqual(self.arca.solicitar_cae.call_count, 1)
        una.refresh_from_db()
        otra.refresh_from_db()
        self.assertEqual((una.numero_intentado, otra.numero_intentado), (42, None))
