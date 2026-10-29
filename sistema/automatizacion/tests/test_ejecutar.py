from datetime import time
from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse

from sistema.asignacion.servicios import crear_cobro_pendiente, fila
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import historial_de, quien
from sistema.cobros.models import Cobro
from sistema.destinatarios.models import Cuenta
from sistema.destinatarios.servicios import (
    cambiar_estado_cuenta,
    cambiar_estado_destinatario,
    crear_cuenta,
    crear_destinatario,
)

from ..servicios import destino, ejecutar
from . import LUNES, ReglasTestCase, en


class DestinoTest(ReglasTestCase):
    def test_destino_medio_con_cinco_cuatro_y_una_cuenta(self):
        self.assertEqual(destino("medio", None, 5), 3)
        self.assertEqual(destino("medio", None, 4), 2)
        self.assertEqual(destino("medio", None, 1), 1)

    def test_destino_ultimo_es_n(self):
        self.assertEqual(destino("ultimo", None, 4), 4)

    def test_destino_posicion_en_el_limite_arriba_y_abajo(self):
        self.assertEqual(destino("posicion", 7, 4), 4)
        self.assertEqual(destino("posicion", 4, 4), 4)
        self.assertEqual(destino("posicion", 3, 4), 3)
        self.assertEqual(destino("posicion", 1, 4), 1)


class EjecutarTest(ReglasTestCase):
    AHORA = en(LUNES, "08:00")

    def correr(self, cuenta, accion, **datos):
        regla = self.regla(cuenta=cuenta, accion=accion, **datos)
        return ejecutar(regla.pk, self.AHORA)

    def sin_huecos(self):
        n = Cuenta.objects.filter(posicion__isnull=False).count()
        pos = sorted(
            Cuenta.objects.filter(posicion__isnull=False).values_list("posicion", flat=True)
        )
        self.assertEqual(pos, list(range(1, n + 1)))

    def test_primero_mueve_la_cuenta_a_la_posicion_1(self):
        self.correr(self.rodrigo, "primero")
        self.assertEqual(
            self.lista(),
            ["rodrigo.mp", "alias.muestra", "jimena.roble.rana", "martin.piedra.casa"],
        )
        self.sin_huecos()

    def test_ultimo_mueve_la_cuenta_a_la_posicion_4(self):
        self.correr(self.tincho, "ultimo")
        self.tincho.refresh_from_db()
        self.assertEqual(self.tincho.posicion, 4)
        self.sin_huecos()

    def test_medio_con_cuatro_y_con_cinco_cuentas(self):
        self.correr(self.martin, "medio")
        self.martin.refresh_from_db()
        self.assertEqual(self.martin.posicion, 2)
        self.sin_huecos()
        crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=crear_destinatario(usuario=None, nombre="Quinta", tipo="profesional"),
            alias="quinta.cuenta",
            tope_mensual=Decimal("500000.00"),
            tope_semanal=Decimal("0.00"),
            minimo_por_pago=Decimal("0.00"),
        )
        self.correr(self.tincho, "medio", nombre="Otra")
        self.tincho.refresh_from_db()
        self.assertEqual(self.tincho.posicion, 3)
        self.sin_huecos()

    def test_posicion_mayor_que_la_lista_se_aplica_en_la_ultima(self):
        evento = self.correr(self.tincho, "posicion", posicion=7)
        self.tincho.refresh_from_db()
        self.assertEqual(self.tincho.posicion, 4)
        self.assertIn("pedida 7, aplicada 4", evento.detalle)
        self.sin_huecos()

    def test_posicion_mayor_con_la_cuenta_ya_en_la_ultima_deja_el_detalle(self):
        evento = self.correr(self.martin, "posicion", posicion=9)
        self.assertEqual(evento.cambios, [])
        self.assertIn("pedida 9, aplicada 4", evento.detalle)

    def test_desactivar_saca_la_cuenta_de_la_lista(self):
        evento = self.correr(self.rodrigo, "desactivar")
        self.rodrigo.refresh_from_db()
        self.assertEqual(self.rodrigo.estado, "inactiva")
        self.assertIsNone(self.rodrigo.posicion)
        self.assertEqual(len(self.lista()), 3)
        self.sin_huecos()
        self.assertTrue(evento.exito)

    def test_activar_pone_la_cuenta_inactiva_al_final(self):
        cambiar_estado_cuenta(self.rodrigo, "inactiva", usuario=None)
        self.correr(self.rodrigo, "activar")
        self.rodrigo.refresh_from_db()
        self.assertEqual((self.rodrigo.estado, self.rodrigo.posicion), ("activa", 4))

    def test_activar_y_desactivar_sobre_archivada_fallan(self):
        cambiar_estado_cuenta(self.rodrigo, "archivada", usuario=None)
        for minuto, accion in enumerate(("activar", "desactivar")):
            evento = self.correr(self.rodrigo, accion, hora=time(7, minuto))
            self.rodrigo.refresh_from_db()
            self.assertEqual(self.rodrigo.estado, "archivada")
            self.assertFalse(evento.exito)
            self.assertIn("no saca una cuenta de Archivada", evento.detalle)

    def test_activar_con_destinatario_inactivo_deja_la_cuenta_fuera_de_la_lista(self):
        cambiar_estado_cuenta(self.rodrigo, "inactiva", usuario=None)
        cambiar_estado_destinatario(self.rodrigo.destinatario, "inactivo", usuario=None)
        self.correr(self.rodrigo, "activar")
        self.rodrigo.refresh_from_db()
        self.assertEqual(self.rodrigo.estado, "activa")
        self.assertIsNone(self.rodrigo.posicion)

    def test_activar_ya_activa_y_desactivar_ya_inactiva_no_cambian_nada(self):
        evento = self.correr(self.rodrigo, "activar")
        self.assertTrue(evento.exito)
        self.assertEqual(evento.cambios, [])
        self.assertIn("sin cambios.", evento.detalle)
        cambiar_estado_cuenta(self.martin, "inactiva", usuario=None)
        evento = self.correr(self.martin, "desactivar", nombre="Otra")
        self.assertTrue(evento.exito)
        self.assertEqual(evento.cambios, [])
        self.assertIn("sin cambios.", evento.detalle)

    def test_desactivar_deja_el_cobro_pendiente_como_estaba(self):
        cobro = crear_cobro_pendiente(monto=Decimal("1000.00"), usuario=self.admin)
        self.assertEqual(cobro.cuenta_id, self.tincho.pk)
        self.correr(self.tincho, "desactivar")
        cobro.refresh_from_db()
        self.assertEqual(cobro.estado, Cobro.Estado.PENDIENTE)

    def test_primero_sobre_la_cuenta_que_ya_es_primera_no_cambia_nada(self):
        evento = self.correr(self.tincho, "primero")
        self.assertTrue(evento.exito)
        self.assertEqual(evento.cambios, [])
        self.assertEqual(self.lista()[0], "alias.muestra")

    def test_mover_cuenta_fuera_de_la_lista_falla_y_marca_la_ejecucion(self):
        cambiar_estado_cuenta(self.jimena, "inactiva", usuario=None)
        cambiar_estado_cuenta(self.rodrigo, "archivada", usuario=None)
        cambiar_estado_destinatario(self.martin.destinatario, "inactivo", usuario=None)
        antes = self.lista()
        for i, cuenta in enumerate((self.jimena, self.rodrigo, self.martin)):
            regla = self.regla(cuenta=cuenta, accion="primero", nombre=f"r{i}")
            evento = ejecutar(regla.pk, self.AHORA)
            regla.refresh_from_db()
            self.assertFalse(evento.exito)
            self.assertIn("La cuenta no está en la lista de prioridad.", evento.detalle)
            self.assertEqual(regla.ultima_ejecucion, self.AHORA)
        self.assertEqual(self.lista(), antes)

    def test_evento_de_ejecucion_y_historial_de_la_cuenta(self):
        regla = self.regla(
            cuenta=self.rodrigo, accion="primero", nombre="Rodrigo primero los lunes"
        )
        evento = ejecutar(regla.pk, self.AHORA)
        origen = "regla 'Rodrigo primero los lunes'"
        self.assertEqual(Evento.objects.filter(tipo="ejecucion_regla").count(), 1)
        self.assertIsNone(evento.usuario)
        self.assertEqual(evento.origen, origen)
        self.assertEqual(evento.entidad, "automatizacion.regla")
        self.assertEqual(
            evento.cambios,
            [{"campo": "posición de la cuenta", "anterior": 3, "nuevo": 1}],
        )
        self.assertTrue(evento.exito)
        self.assertEqual(quien(evento), f"Sistema — {origen}")
        cambio = historial_de(self.rodrigo).filter(tipo="cambio").latest("id")
        self.assertEqual(cambio.origen, origen)
        self.assertEqual(cambio.cambios[0]["campo"], "posicion")
        self.assertNotIn(self.rodrigo.cbu or "sin-cbu", evento.detalle)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("auditoria:historial", args=["automatizacion.regla", regla.pk]))
        for texto in ("Sistema — regla", "Éxito", "posición de la cuenta"):
            self.assertContains(r, texto)

    def test_ejecutar_toma_la_fila_antes_de_tocar_la_cuenta(self):
        regla = self.regla(cuenta=self.rodrigo, accion="primero")
        with patch("sistema.automatizacion.servicios.fila", wraps=fila) as f:
            ejecutar(regla.pk, self.AHORA)
        f.assert_called()

    def test_ejecutar_regla_inactiva_devuelve_none_sin_evento(self):
        regla = self.regla(cuenta=self.rodrigo, accion="primero", estado="inactiva")
        self.assertIsNone(ejecutar(regla.pk, self.AHORA))
        self.assertFalse(Evento.objects.filter(tipo="ejecucion_regla").exists())
