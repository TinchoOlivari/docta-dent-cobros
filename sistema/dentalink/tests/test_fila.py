import threading
from unittest.mock import patch

from django.db import connection

from sistema.core.tests import SistemaTransactionTestCase
from sistema.dentalink import servicios
from sistema.dentalink.cliente import Resultado
from sistema.dentalink.tests import cobro_con_items, copia_basica


class FilaTest(SistemaTransactionTestCase):
    def test_dos_hilos_sobre_el_mismo_item_hacen_una_sola_llamada(self):
        copia_basica()
        item = cobro_con_items(["1000.00"]).items.get()
        llamado, seguir = threading.Event(), threading.Event()
        llamadas, resultados = [], {}

        def crear_pago(datos):
            llamadas.append(datos)
            llamado.set()
            seguir.wait(5)
            return Resultado("ok", id_pago=1)

        def correr(nombre):
            try:
                resultados[nombre] = servicios.enviar_item(item.pk)
            finally:
                connection.close()

        with patch("sistema.dentalink.servicios.cliente.crear_pago", side_effect=crear_pago):
            h1 = threading.Thread(target=correr, args=("h1",))
            h1.start()
            self.assertTrue(llamado.wait(5))  # en_curso_desde ya está confirmado
            h2 = threading.Thread(target=correr, args=("h2",))
            h2.start()
            h2.join(10)
            seguir.set()
            h1.join(10)
        self.assertEqual(len(llamadas), 1)
        self.assertEqual(sorted(resultados.values()), ["enviado", "omitido"])
        self.assertEqual(resultados["h1"], "enviado")
