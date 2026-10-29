from datetime import time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from ..models import Regla
from . import ReglasTestCase

MSG_DIA = "El día debe ir de 1 a 7 para la semana y de 1 a 31 para el mes."
MSG_POS = "La posición específica debe ser 1 o más."


class ModeloReglaTest(ReglasTestCase):
    def datos(self, **kw):
        d = dict(
            nombre="R",
            cuenta=self.rodrigo,
            disparador="semana",
            dia=1,
            hora=time(8, 0),
            accion="primero",
        )
        d.update(kw)
        return d

    def valida(self, **kw):
        Regla(**self.datos(**kw)).full_clean()

    def rechaza(self, msg, **kw):
        with self.assertRaises(ValidationError) as cm:
            self.valida(**kw)
        self.assertIn(msg, str(cm.exception))

    def test_dia_del_mes_0_y_32_rechazan(self):
        for dia in (0, 32):
            self.rechaza(MSG_DIA, disparador="mes", dia=dia)

    def test_dia_del_mes_1_y_31_pasan(self):
        for dia in (1, 31):
            self.valida(disparador="mes", dia=dia)

    def test_dia_de_semana_0_y_8_rechazan(self):
        for dia in (0, 8):
            self.rechaza(MSG_DIA, dia=dia)

    def test_dia_de_semana_1_y_7_pasan(self):
        for dia in (1, 7):
            self.valida(dia=dia)

    def test_posicion_sin_numero_o_cero_rechaza(self):
        for pos in (None, 0):
            self.rechaza(MSG_POS, accion="posicion", posicion=pos)

    def test_posicion_1_pasa(self):
        self.valida(accion="posicion", posicion=1)

    def test_primero_con_posicion_rechaza(self):
        self.rechaza(MSG_POS, accion="primero", posicion=2)

    def test_cuenta_pos_rechaza(self):
        self.rechaza("Las reglas actúan solo sobre cuentas de transferencia.", cuenta=self.pos)

    def falla_db(self, **kw):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Regla.objects.create(**self.datos(**kw))

    def test_db_rechaza_dia_32(self):
        self.falla_db(disparador="mes", dia=32)

    def test_db_rechaza_posicion_0(self):
        self.falla_db(accion="posicion", posicion=0)

    def test_db_rechaza_dos_activas_iguales(self):
        Regla.objects.create(**self.datos())
        self.falla_db()

    def test_db_acepta_iguales_si_una_es_inactiva(self):
        Regla.objects.create(**self.datos())
        Regla.objects.create(**self.datos(estado="inactiva"))
        Regla.objects.create(**self.datos(estado="inactiva"))
        self.assertEqual(Regla.objects.count(), 3)

    def test_nada_se_borra(self):
        r = Regla.objects.create(**self.datos())
        with self.assertRaises(ProtectedError):
            r.delete()
        with self.assertRaises(ProtectedError):
            Regla.objects.all().delete()
        self.assertEqual(Regla.objects.count(), 1)
