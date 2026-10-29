from datetime import UTC, date, datetime
from decimal import Decimal as D
from unittest.mock import patch

from django.core import mail

from sistema.asignacion.servicios import usado_mes
from sistema.auditoria.models import Evento
from sistema.cobros.models import Cobro, PagoComplementario
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import tope_de
from sistema.migracion import servicios
from sistema.migracion.tests import fabricas as f

AHORA = datetime(2026, 11, 20, 12, 0, tzinfo=ARG)


class ComplementariosBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.comp = f.usuario("completar", role="professional")
        self.ana = f.usuario("ana")
        self.perez = f.usuario("drperez", role="professional")
        self.fila = f.fila("Jimena", alias="jimena.roble.rana", max_amount=10000)

    def pago(self, amount, created_at=f.CREADO, **kw):
        return f.pago(self.fila, amount, self.ana, self.comp, created_at=created_at, **kw)

    def migrar(self):
        datos = {"para_completar_pagos": "completar", "tipos": {}, "vinculos": {}}
        with patch("django.utils.timezone.now", return_value=AHORA):
            servicios.migrar(datos, definitiva=True)


class ComplementarioTest(ComplementariosBase):
    def test_pago_de_completar_pasa_como_complementario_registrado(self):
        p = self.pago(8000, created_at=datetime(2026, 10, 28, 15, 0, tzinfo=ARG), archivo="c1.pdf")
        self.migrar()
        c = PagoComplementario.objects.get()
        self.assertEqual(
            (c.estado, c.monto, c.metodo), ("registrado", D("8000.00"), "transferencia")
        )
        self.assertEqual((c.destinatario.nombre, c.cuenta.alias), ("Jimena", "jimena.roble.rana"))
        self.assertEqual(c.mes, date(2026, 10, 1))
        self.assertEqual(c.creado_por, self.ana)
        self.assertEqual(c.comprobante.name, p.proof_of_payment_file.name)
        self.assertFalse(Cobro.objects.filter(pk=p.pk).exists())
        self.assertEqual(Evento.objects.filter(entidad="cobros.pagocomplementario").count(), 1)

    def test_sin_comprobante_queda_vacio(self):
        self.pago(100, observaciones="nota")
        self.migrar()
        c = PagoComplementario.objects.get()
        self.assertEqual((c.comprobante.name, c.observaciones), ("", "nota"))

    def test_mes_en_hora_argentina_31_oct_2359(self):
        # 31/10 23:59 ARG = 1/11 02:59 UTC
        self.pago(100, created_at=datetime(2026, 11, 1, 2, 59, tzinfo=UTC))
        self.migrar()
        self.assertEqual(PagoComplementario.objects.get().mes, date(2026, 10, 1))

    def test_mes_en_hora_argentina_1_nov_0000(self):
        self.pago(100, created_at=datetime(2026, 11, 1, 0, 0, tzinfo=ARG))
        self.migrar()
        self.assertEqual(PagoComplementario.objects.get().mes, date(2026, 11, 1))

    def test_pasado_el_tope_se_guarda_igual(self):
        self.pago(12000)
        self.migrar()
        self.assertEqual(PagoComplementario.objects.get().monto, D("12000.00"))

    def test_cinco_pagos_suman_50000(self):
        for _ in range(5):
            self.pago(10000)
        self.migrar()
        montos = PagoComplementario.objects.values_list("monto", flat=True)
        self.assertEqual((len(montos), sum(montos)), (5, D("50000.00")))
        self.assertEqual(Cobro.objects.count(), 0)

    def test_no_dispara_avisos(self):
        from sistema.avisos.models import DireccionAviso

        DireccionAviso.objects.create(email="aviso@ejemplo.test")
        self.pago(12000)
        with (
            patch("sistema.avisos.tasks.enviar_aviso.delay") as aviso,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            self.migrar()
        self.assertEqual(callbacks, [])
        self.assertEqual(aviso.call_count, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_control_un_aviso_si_se_encola_al_commit(self):
        # Sin este control, el test anterior pasaría aunque el patch no sirviera.
        from sistema.avisos import servicios as avisos
        from sistema.avisos.models import DireccionAviso

        DireccionAviso.objects.create(email="aviso@ejemplo.test")
        with (
            patch("sistema.avisos.tasks.enviar_aviso.delay") as aviso,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            avisos._enviar("Asunto", ["Cuerpo"])
        self.assertEqual(len(callbacks), 1)
        aviso.assert_called_once_with("aviso@ejemplo.test", "Asunto", "Cuerpo")

    def test_cupo_del_mes_coincide_con_el_viejo(self):
        self.pago(2000, created_at=datetime(2026, 10, 5, 10, 0, tzinfo=ARG))
        f.pago(self.fila, 3000, self.ana, self.perez, created_at=f.CREADO)
        f.pago(self.fila, 1000, self.ana, self.perez, created_at=f.CREADO)
        self.migrar()
        ahora = datetime(2026, 10, 20, 12, 0, tzinfo=ARG)
        cuenta = PagoComplementario.objects.get().cuenta
        with patch("django.utils.timezone.now", return_value=ahora):
            self.fila.refresh_from_db()
            restante = D(self.fila.get_remaining_amount())
            self.assertEqual(restante, tope_de(cuenta, "mensual", ahora) - usado_mes(cuenta, ahora))
