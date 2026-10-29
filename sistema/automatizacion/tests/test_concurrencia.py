import threading
from decimal import Decimal

from django.db import connection

from sistema.asignacion.servicios import crear_cobro_pendiente
from sistema.auditoria.models import Evento
from sistema.destinatarios.models import Cuenta

from ..servicios import ejecutar
from . import LUNES, ReglasTransaccionTestCase, en


def en_paralelo(*trabajos):
    """Cada trabajo corre en su hilo; la barrera los suelta juntos. Falla si un hilo falla."""
    barrera = threading.Barrier(len(trabajos), timeout=30)
    errores = []

    def envolver(trabajo):
        try:
            barrera.wait()
            trabajo()
        except BaseException as e:  # noqa: BLE001
            errores.append(e)
        finally:
            connection.close()

    hilos = [threading.Thread(target=envolver, args=(t,)) for t in trabajos]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=60)
    return errores


class ConcurrenciaReglasTest(ReglasTransaccionTestCase):
    def setUp(self):
        super().setUp()
        self.ahora = en(LUNES, "08:00")

    def sin_huecos(self):
        pos = sorted(
            Cuenta.objects.filter(posicion__isnull=False).values_list("posicion", flat=True)
        )
        self.assertEqual(pos, list(range(1, len(pos) + 1)))

    def test_dos_ejecuciones_a_la_vez_dejan_un_solo_evento(self):
        regla = self.regla(cuenta=self.rodrigo)
        errores = en_paralelo(
            lambda: ejecutar(regla.pk, self.ahora), lambda: ejecutar(regla.pk, self.ahora)
        )
        self.assertEqual(errores, [])
        self.assertEqual(Evento.objects.filter(tipo="ejecucion_regla").count(), 1)
        self.assertEqual(self.lista()[0], "rodrigo.mp")
        self.sin_huecos()

    def test_desactivar_y_pedido_de_cuenta_a_la_vez_se_atienden_en_fila(self):
        regla = self.regla(cuenta=self.tincho, accion="desactivar")
        errores = en_paralelo(
            lambda: ejecutar(regla.pk, self.ahora),
            lambda: crear_cobro_pendiente(monto=Decimal("1000.00"), usuario=None),
        )
        self.assertEqual(errores, [])
        cobro = Evento.objects.filter(tipo="alta", entidad="cobros.cobro").get()
        cambio = Evento.objects.filter(tipo="cambio", entidad="destinatarios.cuenta").get(
            entidad_id=self.tincho.pk
        )
        from sistema.cobros.models import Cobro

        esperado = self.tincho if cobro.id < cambio.id else self.jimena
        self.assertEqual(Cobro.objects.get().cuenta_id, esperado.pk)
        self.sin_huecos()
