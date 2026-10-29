from datetime import time

from django.core.exceptions import ValidationError

from sistema.auditoria.models import Evento

from ..models import Regla
from ..servicios import cambiar_estado_reglas, editar_regla
from . import LUNES, ReglasTestCase, en


class CrearReglaTests(ReglasTestCase):
    def test_crear_regla_activa_deja_fecha_activacion_y_evento_alta(self):
        r = self.regla(nombre="Rodrigo primero los lunes", cuenta=self.rodrigo, accion="primero")
        self.assertEqual(r.estado, "activa")
        self.assertEqual(r.fecha_activacion, en(LUNES, "07:00"))
        ev = Evento.objects.get(tipo="alta", entidad="automatizacion.regla")
        self.assertEqual(ev.usuario, self.admin)

    def test_crear_regla_inactiva_no_tiene_fecha_activacion(self):
        r = self.regla(cuenta=self.rodrigo, estado="inactiva")
        self.assertIsNone(r.fecha_activacion)


class UnicaActivaTests(ReglasTestCase):
    def test_segunda_regla_activa_igual_falla(self):
        self.regla(cuenta=self.rodrigo)
        with self.assertRaisesMessage(ValidationError, "Ya hay una regla Activa"):
            self.regla(cuenta=self.rodrigo)

    def test_variantes_de_hora_disparador_o_cuenta_pasan(self):
        self.regla(cuenta=self.rodrigo)
        self.regla(cuenta=self.rodrigo, hora=time(8, 1))
        self.regla(cuenta=self.rodrigo, disparador="mes", dia=1)
        self.regla(cuenta=self.tincho)
        self.assertEqual(Regla.objects.count(), 4)

    def test_editar_a_duplicado_falla_y_deja_la_regla_igual(self):
        self.regla(cuenta=self.rodrigo, dia=1)
        r = self.regla(cuenta=self.rodrigo, dia=2)
        with self.assertRaisesMessage(ValidationError, "Ya hay una regla Activa"):
            editar_regla(r, usuario=self.admin, dia=1)
        r.refresh_from_db()
        self.assertEqual(r.dia, 2)

    def test_activar_inactiva_igual_a_una_activa_falla(self):
        self.regla(cuenta=self.rodrigo)
        r = self.regla(cuenta=self.rodrigo, estado="inactiva")
        with self.assertRaisesMessage(ValidationError, "choca con"):
            cambiar_estado_reglas([r], "activa", usuario=self.admin)
        r.refresh_from_db()
        self.assertEqual(r.estado, "inactiva")

    def test_activar_dos_inactivas_iguales_juntas_es_todo_o_nada(self):
        r1 = self.regla(cuenta=self.rodrigo, estado="inactiva")
        r2 = self.regla(cuenta=self.rodrigo, estado="inactiva")
        with self.assertRaises(ValidationError):
            cambiar_estado_reglas([r1, r2], "activa", usuario=self.admin)
        self.assertEqual(Regla.objects.filter(estado="activa").count(), 0)


class CambiarEstadoYEditarTests(ReglasTestCase):
    def test_cambiar_estado_devuelve_cuantas_cambiaron_y_es_idempotente(self):
        rs = [self.regla(cuenta=c) for c in (self.rodrigo, self.tincho, self.jimena)]
        self.assertEqual(cambiar_estado_reglas(rs, "inactiva", usuario=self.admin), 3)
        self.assertEqual(Evento.objects.filter(tipo="cambio").count(), 3)
        self.assertEqual(cambiar_estado_reglas(rs, "inactiva", usuario=self.admin), 0)
        self.assertEqual(Evento.objects.filter(tipo="cambio").count(), 3)

    def test_activar_deja_fecha_activacion_nueva(self):
        r = self.regla(cuenta=self.rodrigo, estado="inactiva")
        cambiar_estado_reglas([r], "activa", usuario=self.admin)
        r.refresh_from_db()
        self.assertEqual(r.fecha_activacion, en(LUNES, "07:00"))

    def test_editar_nombre_deja_un_evento_cambio_con_nombre(self):
        r = self.regla(cuenta=self.rodrigo)
        editar_regla(r, usuario=self.admin, nombre="Rodrigo primero")
        ev = Evento.objects.get(tipo="cambio")
        self.assertEqual([c["campo"] for c in ev.cambios], ["nombre"])

    def test_editar_sin_cambios_no_deja_evento(self):
        r = self.regla(cuenta=self.rodrigo)
        editar_regla(r, usuario=self.admin, nombre=r.nombre)
        self.assertEqual(Evento.objects.filter(tipo="cambio").count(), 0)

    def test_editar_estado_falla(self):
        r = self.regla(cuenta=self.rodrigo)
        with self.assertRaisesMessage(ValidationError, "El estado se cambia con"):
            editar_regla(r, usuario=self.admin, estado="inactiva")
