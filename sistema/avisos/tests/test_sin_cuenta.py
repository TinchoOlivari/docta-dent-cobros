from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.core import mail

from sistema.asignacion.servicios import SinCuentaDisponible, crear_cobro_pendiente
from sistema.auditoria.models import Evento
from sistema.avisos.models import DireccionAviso
from sistema.avisos.tests.test_tope import BaseTope
from sistema.core.tiempo import ARG

D = Decimal


class SinCuentaTests(BaseTope):
    def pedir(self, monto="250000.00"):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(SinCuentaDisponible):
                crear_cobro_pendiente(
                    monto=D(monto),
                    usuario=self.ana,
                    nombre_paciente="Juan Paciente",
                    id_paciente_dentalink=501,
                )

    def setUp(self):
        super().setUp()
        self.usado("300000.00")  # cupo 200000

    def test_tres_rechazos_mandan_tres_avisos_por_direccion(self):
        for _ in range(3):
            self.pedir()
        self.assertEqual(len(mail.outbox), 6)
        m = mail.outbox[0]
        self.assertEqual(m.subject, "No hay cuenta disponible para $250.000,00")
        for texto in ("ana", "Juan Paciente", "20/10/2026 10:00:00", "Monto pedido: $250.000,00"):
            self.assertIn(texto, m.body)

    def test_sin_direcciones_el_rechazo_sigue_igual(self):
        DireccionAviso.objects.all().delete()
        self.pedir()
        self.assertEqual(mail.outbox, [])

    def test_no_crea_eventos(self):
        antes = Evento.objects.count()
        self.pedir()
        self.assertEqual(Evento.objects.count(), antes)

    def test_hora_a_las_23_59_59_del_31_de_octubre(self):
        fin = datetime(2026, 10, 31, 23, 59, 59, tzinfo=ARG)
        with mock.patch("django.utils.timezone.now", return_value=fin):
            self.pedir("500000.01")
        self.assertIn("31/10/2026 23:59:59", mail.outbox[0].body)

    def test_cobro_justo_con_el_cupo_no_manda_aviso_de_sin_cuenta(self):
        with self.captureOnCommitCallbacks(execute=True):
            crear_cobro_pendiente(
                monto=D("200000.00"), usuario=self.ana, nombre_paciente="X", id_paciente_dentalink=1
            )
        self.assertNotIn("No hay cuenta", " ".join(self.asuntos()))

    def test_fallo_posterior_al_alta_no_manda_cerca_del_tope(self):
        self.usado("120000.00")  # 420000
        with self.captureOnCommitCallbacks(execute=True):
            try:
                from django.db import transaction

                with transaction.atomic():
                    crear_cobro_pendiente(
                        monto=D("30000.00"),
                        usuario=self.ana,
                        nombre_paciente="X",
                        id_paciente_dentalink=1,
                    )
                    raise RuntimeError("Dentalink cayó")
            except RuntimeError:
                pass
        self.assertEqual(mail.outbox, [])
