from datetime import datetime, time

from django.contrib.admin.models import LogEntry
from unittest.mock import patch

from django.core import mail

from sistema.auditoria.models import Evento
from sistema.automatizacion.models import Regla
from sistema.automatizacion.servicios import correr_reglas
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta
from sistema.migracion import servicios
from sistema.migracion.tests import fabricas as f

ULTIMA = datetime(2026, 10, 12, 8, 30, tzinfo=ARG)


class ReglasBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        f.usuario("completar", role="professional")
        self.fila = f.fila("Rodrigo", alias="rodrigo.mp")

    def migrar(self):
        datos = {"para_completar_pagos": "completar", "tipos": {}, "vinculos": {}}
        with self.captureOnCommitCallbacks(execute=True):
            servicios.migrar(datos, definitiva=True)

    def regla(self, **kw):
        kw.setdefault("trigger_time", time(8, 30))
        return f.regla(self.fila, **kw)


class ReglasTest(ReglasBase):
    def test_regla_semanal_pasa_sobre_su_cuenta_con_su_ultima_ejecucion(self):
        self.regla(
            name="Rodrigo primero los lunes",
            action_type="SET_PRIORITY",
            priority_position="FIRST",
            active_value=None,
            last_executed=ULTIMA,
        )
        self.migrar()
        r = Regla.objects.get()
        self.assertEqual(
            (r.nombre, r.cuenta.alias, r.disparador, r.dia, r.hora, r.accion, r.estado),
            (
                "Rodrigo primero los lunes",
                "rodrigo.mp",
                "semana",
                1,
                time(8, 30),
                "primero",
                "activa",
            ),
        )
        self.assertEqual(r.ultima_ejecucion, ULTIMA)

    def test_domingo_pasa_como_dia_7(self):
        self.regla(trigger_weekday=6)
        self.migrar()
        self.assertEqual(Regla.objects.get().dia, 7)

    def test_dia_del_mes_31(self):
        self.regla(rule_type="MONTHDAY", trigger_weekday=None, trigger_monthday=31)
        self.migrar()
        r = Regla.objects.get()
        self.assertEqual((r.disparador, r.dia), ("mes", 31))

    def test_posicion_especifica_3(self):
        self.regla(action_type="SET_PRIORITY", priority_position="SPECIFIC", priority_value=3)
        self.migrar()
        r = Regla.objects.get()
        self.assertEqual((r.accion, r.posicion), ("posicion", 3))

    def test_acciones_ultimo_medio_y_desactivar(self):
        self.regla(
            name="u", action_type="SET_PRIORITY", priority_position="LAST", trigger_weekday=1
        )
        self.regla(
            name="m", action_type="SET_PRIORITY", priority_position="MIDDLE", trigger_weekday=2
        )
        self.regla(name="d", active_value=False, trigger_weekday=3)
        self.migrar()
        acciones = dict(Regla.objects.values_list("nombre", "accion"))
        self.assertEqual(acciones, {"u": "ultimo", "m": "medio", "d": "desactivar"})

    def test_regla_inactiva_en_prod_pasa_inactiva(self):
        self.regla(is_active=False)
        self.migrar()
        self.assertEqual(Regla.objects.get().estado, "inactiva")

    def test_regla_sobre_fila_de_senas_pasa_inactiva(self):
        sena = f.fila(
            "Solo para señas", alias="solo.senas", recipient_type="DEPOSIT_ONLY", priority_order=2
        )
        f.regla(sena, trigger_time=time(8, 30))
        self.migrar()
        r = Regla.objects.get()
        self.assertEqual((r.estado, r.cuenta.estado), ("inactiva", "archivada"))
        self.assertIsNone(r.fecha_activacion)

    def test_300_ejecuciones_pasan_con_su_fecha_original(self):
        r = self.regla(action_type="SET_PRIORITY", priority_position="FIRST")
        fecha = datetime(2026, 9, 7, 8, 30, tzinfo=ARG)
        for _ in range(300):
            f.ejecucion(r, executed_at=fecha, old_value="2", new_value="1")
        self.migrar()
        eventos = Evento.objects.filter(tipo="ejecucion_regla")
        self.assertEqual(eventos.count(), 300)
        e = eventos.first()
        self.assertEqual(
            (e.fecha, e.cambios, e.exito, e.entidad_id, e.usuario),
            (
                fecha,
                [{"campo": "posicion", "anterior": "2", "nuevo": "1"}],
                True,
                Regla.objects.get().pk,
                None,
            ),
        )

    def test_ejecucion_fallida_guarda_el_error_y_exito_falso(self):
        r = self.regla()
        f.ejecucion(r, success=False, error_message="sin cuenta")
        self.migrar()
        e = Evento.objects.get(tipo="ejecucion_regla")
        self.assertIs(e.exito, False)
        self.assertIn("sin cuenta", e.detalle)
        self.assertEqual(e.cambios[0]["campo"], "estado")

    def test_cada_regla_y_usuario_tiene_un_evento_de_alta_y_el_log_del_admin_no_pasa(self):
        self.regla()
        admin = f.usuario("root", is_superuser=True)
        LogEntry.objects.create(user_id=admin.pk, object_repr="x", action_flag=1, change_message="")
        self.migrar()
        altas = Evento.objects.filter(tipo="alta", origen=servicios.ORIGEN)
        self.assertEqual(altas.filter(entidad="automatizacion.regla").count(), 1)
        self.assertEqual(altas.filter(entidad="payment_instructions.user").count(), 1)
        self.assertFalse(Evento.objects.filter(entidad__icontains="logentry").exists())

    def test_regla_migrada_no_corre_al_migrar(self):
        # Lunes 2026-10-19 09:00: las 08:30 ya pasaron y la regla nunca corrió; igual no corre.
        from sistema.avisos.models import DireccionAviso

        DireccionAviso.objects.create(email="aviso@ejemplo.test")
        self.regla(action_type="SET_PRIORITY", priority_position="LAST")
        f.fila("Otra", alias="otra.cuenta", priority_order=2)
        lunes = datetime(2026, 10, 19, 9, 0, tzinfo=ARG)
        with patch("sistema.avisos.tasks.enviar_aviso.delay") as aviso:
            self.migrar_el(lunes)
        self.assertEqual(Evento.objects.filter(tipo="ejecucion_regla").count(), 0)
        self.assertEqual(aviso.call_count, 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Cuenta.objects.get(alias="rodrigo.mp").posicion, 1)

    def migrar_el(self, ahora):
        with patch("django.utils.timezone.now", return_value=ahora):
            self.migrar()

    def test_regla_ejecutada_hoy_no_corre_otra_vez_ese_dia(self):
        self.regla(
            action_type="SET_PRIORITY",
            priority_position="LAST",
            last_executed=datetime(2026, 10, 19, 8, 30, tzinfo=ARG),
        )
        f.fila("Otra", alias="otra.cuenta", priority_order=2)
        self.migrar_el(datetime(2026, 10, 18, 12, 0, tzinfo=ARG))
        self.assertEqual(correr_reglas(datetime(2026, 10, 19, 9, 0, tzinfo=ARG)), 0)
        self.assertEqual(Cuenta.objects.get(alias="rodrigo.mp").posicion, 1)

    def test_regla_sin_ejecutar_corre_una_vez_el_dia_que_toca(self):
        # Control del test anterior: sin ultima_ejecucion, la misma regla sí corre.
        self.regla(action_type="SET_PRIORITY", priority_position="LAST")
        f.fila("Otra", alias="otra.cuenta", priority_order=2)
        self.migrar_el(datetime(2026, 10, 18, 12, 0, tzinfo=ARG))
        ahora = datetime(2026, 10, 19, 9, 0, tzinfo=ARG)
        self.assertEqual(correr_reglas(ahora), 1)
        self.assertEqual(correr_reglas(ahora), 0)
        self.assertEqual(Cuenta.objects.get(alias="rodrigo.mp").posicion, 2)

    def test_regla_migrada_el_mismo_dia_no_corre_aunque_su_hora_ya_paso(self):
        # FR-012: migrada a las 12:00, la regla de las 08:30 de hoy no corre.
        self.regla(action_type="SET_PRIORITY", priority_position="LAST")
        f.fila("Otra", alias="otra.cuenta", priority_order=2)
        self.migrar_el(datetime(2026, 10, 19, 12, 0, tzinfo=ARG))
        self.assertEqual(correr_reglas(datetime(2026, 10, 19, 13, 0, tzinfo=ARG)), 0)
