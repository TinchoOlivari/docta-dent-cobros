from datetime import datetime, time
from decimal import Decimal as D
from unittest.mock import patch

from django.core.management.base import CommandError

from sistema.cobros.models import Cobro, PagoComplementario
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta
from sistema.migracion import servicios
from sistema.migracion.tests import fabricas as f

AHORA = datetime(2026, 11, 20, 12, 0, tzinfo=ARG)
OCT = datetime(2026, 10, 10, 14, 5, tzinfo=ARG)
NOV = datetime(2026, 11, 3, 9, 0, tzinfo=ARG)


class VerificacionBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.comp = f.usuario("completar", role="professional")
        self.ana = f.usuario("ana")
        self.perez = f.usuario("drperez", role="professional")
        self.tincho = f.fila("Tincho", alias="Alias.Uno", max_amount=100000, priority_order=1)
        self.rodri = f.fila("Rodrigo", alias="rodrigo.mp", priority_order=2)
        self.sena = f.fila(
            "Señas", alias="senas.prueba", recipient_type="DEPOSIT_ONLY", is_active=False
        )
        self.datos = {"para_completar_pagos": "completar", "tipos": {}, "vinculos": {}}

    def migrar(self, definitiva=True):
        with patch("django.utils.timezone.now", return_value=AHORA):
            return servicios.migrar(self.datos, definitiva=definitiva)

    def datos_completos(self):
        f.pago(self.tincho, 15000, self.ana, self.perez, created_at=OCT)
        f.pago(self.tincho, 1, self.ana, self.perez, created_at=NOV)
        f.pago(self.rodri, 3200, self.ana, self.perez, created_at=OCT)
        f.pago(self.sena, 5000, self.ana, self.perez, created_at=OCT, payment_type="DEPOSIT")
        f.pago(self.rodri, 700, self.ana, self.comp, created_at=NOV)
        regla = f.regla(self.rodri, trigger_time=time(8, 30))
        f.ejecucion(regla, executed_at=OCT)
        f.ejecucion(regla, executed_at=NOV)


class VerificarTest(VerificacionBase):
    def test_datos_de_las_historias_no_dan_diferencias(self):
        self.datos_completos()
        informe = self.migrar()
        self.assertEqual(informe.diferencias, [])
        self.assertEqual(servicios.verificar(self.datos), [])
        self.assertIn("Verificación: 0 diferencias", informe.texto())

    def test_pago_de_un_peso_no_da_diferencia(self):
        f.pago(self.tincho, 1, self.ana, self.perez)
        self.assertEqual(self.migrar().diferencias, [])
        self.assertEqual(Cobro.objects.get().monto, D("1.00"))

    def test_sin_pagos_no_da_diferencias(self):
        self.assertEqual(self.migrar().diferencias, [])

    def test_revision_tambien_verifica_y_no_deja_datos(self):
        self.datos_completos()
        informe = self.migrar(definitiva=False)
        self.assertEqual(informe.diferencias, [])
        self.assertFalse(Cobro.objects.exists())
        self.assertFalse(Cuenta.objects.filter(tipo="transferencia").exists())

    def test_revision_con_diferencia_la_informa_sin_fallar(self):
        f.pago(self.tincho, 100, self.ana, self.perez)
        with patch.object(servicios, "_cobros", lambda datos, cuentas: {}):
            informe = self.migrar(definitiva=False)
        self.assertIn("Cobros: prod 1, nuevo 0", informe.diferencias)
        self.assertIn("Verificación: 2 diferencias", informe.texto())

    def test_suma_distinta_en_un_mes_se_lista_con_cuenta_y_mes(self):
        f.pago(self.tincho, 15000, self.ana, self.perez, created_at=OCT)
        f.pago(self.tincho, 1, self.ana, self.perez, created_at=NOV)
        with patch.object(servicios, "_montos_nuevos", return_value={}):
            with self.assertRaises(CommandError) as ctx:
                self.migrar()
        lineas = str(ctx.exception).splitlines()
        self.assertIn('Cuenta "alias.uno", 2026-10: prod $15.000,00, nuevo $0,00', lineas)
        self.assertIn('Cuenta "alias.uno", 2026-11: prod $1,00, nuevo $0,00', lineas)


class DiferenciaForzadaTest(VerificacionBase):
    def test_un_cobro_omitido_deshace_todo(self):
        for _ in range(3):
            f.pago(self.tincho, 1000, self.ana, self.perez)
        original = servicios._cobros

        def omite_uno(datos, cuentas):
            pagos = servicios.Payment.objects.exclude(pk=servicios.Payment.objects.first().pk)
            with patch.object(servicios.Payment, "objects", pagos):
                return original(datos, cuentas)

        with patch.object(servicios, "_cobros", omite_uno):
            with self.assertRaises(CommandError) as ctx:
                self.migrar()
        self.assertIn("Cobros: prod 3, nuevo 2", str(ctx.exception))
        self.assertIn(
            'Cuenta "alias.uno", 2026-10: prod $3.000,00, nuevo $2.000,00', str(ctx.exception)
        )
        self.assertFalse(Cobro.objects.exists())
        self.assertFalse(Cuenta.objects.filter(tipo="transferencia").exists())
        self.assertFalse(PagoComplementario.objects.exists())
