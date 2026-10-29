"""022 fase E: comentario del operador vacío (FR-026) y pantalla de avisos (FR-025)."""

from decimal import Decimal as D

from django.core import mail

from sistema.avisos.tests.test_tope import BaseTope
from sistema.cobros import servicios


class ComentarioExcedenteTest(BaseTope):
    def excedente(self, observaciones):
        self.usado("400000.00")
        c = self.usado("50000.00")
        c.observaciones = observaciones
        c.save()
        with self.captureOnCommitCallbacks(execute=True):
            servicios.editar_cobro(
                c, usuario=self.admin, estado_visto="pendiente", monto=D("120000.00")
            )
        return next(m.body for m in mail.outbox if m.subject.startswith("Cobro excedente"))

    def test_observaciones_vacias_salen_como_raya(self):
        self.assertIn("Comentario del operador: —", self.excedente(""))

    def test_observaciones_con_texto_salen_tal_cual(self):
        self.assertIn(
            "Comentario del operador: pagó en 2 partes", self.excedente("pagó en 2 partes")
        )
