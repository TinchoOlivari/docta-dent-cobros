from decimal import Decimal

from django.core import mail

from datetime import datetime

from sistema.avisos import servicios as avisos
from sistema.avisos.tests.test_tope import BaseTope
from sistema.cobros import servicios
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, editar_cuenta

D = Decimal


class ExcedenteTests(BaseTope):
    def editar(self, cobro, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            return servicios.editar_cobro(cobro, usuario=self.admin, estado_visto="pendiente", **kw)

    def test_edicion_que_pasa_el_tope_avisa_excedente(self):
        self.usado("400000.00")
        c = self.usado("50000.00")  # usado 450000: ya arriba del umbral
        c.observaciones = "Pidió seña"
        c.save()
        self.editar(c, monto=D("120000.00"))  # 520000
        self.assertEqual(len(mail.outbox), 2)
        m = mail.outbox[0]
        self.assertEqual(m.subject, "Cobro excedente: cuenta Destinatario Ejemplo — alias.uno")
        for texto in ("Excedente: $20.000,00", f"Cobro: {c.pk}", "$120.000,00", "Transferencia"):
            self.assertIn(texto, m.body)
        self.assertIn(f"/sistema/cobros/{c.pk}/", m.body)
        c.refresh_from_db()
        self.assertEqual((c.excedente, c.revisado), (True, False))

    def test_edicion_justo_en_el_tope_no_es_excedente_y_un_centavo_mas_si(self):
        self.usado("400000.00")
        c = self.usado("50000.00")
        self.editar(c, monto=D("100000.00"))  # 500000 justo
        self.assertNotIn("excedente", " ".join(self.asuntos()))
        self.editar(c, monto=D("100000.01"))
        self.assertEqual(
            self.asuntos().count("Cobro excedente: cuenta Destinatario Ejemplo — alias.uno"), 2
        )

    def test_cruza_el_umbral_y_el_tope_en_una_edicion_manda_dos_avisos_en_orden(self):
        self.usado("370000.00")
        c = self.usado("50000.00")  # 420000
        self.editar(c, monto=D("150000.00"))  # 520000
        self.assertEqual(
            self.asuntos()[::2],
            [
                "Cuenta Destinatario Ejemplo — alias.uno cerca del tope",
                "Cobro excedente: cuenta Destinatario Ejemplo — alias.uno",
            ],
        )
        self.assertEqual(len(mail.outbox), 4)

    def test_ya_excedente_que_sigue_arriba_no_avisa(self):
        self.usado("450000.00")
        c = self.usado("60000.00")
        c.excedente = True
        c.save()
        self.editar(c, monto=D("70000.00"))
        self.assertEqual(mail.outbox, [])

    def test_mover_a_otra_cuenta_que_cruza_el_umbral_avisa_de_la_cuenta_nueva(self):
        otra = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="otra.cuenta",
            tope_mensual=D("100000.00"),
        )
        c = self.usado("10000.00")
        self.usado("85000.00")
        from sistema.cobros.models import Cobro

        Cobro.objects.filter(cuenta=self.cuenta, monto=D("85000.00")).update(cuenta=otra)
        self.editar(c, cuenta=otra)  # 95000 >= 90000
        self.assertEqual(
            self.asuntos(), ["Cuenta Destinatario Ejemplo — otra.cuenta cerca del tope"] * 2
        )


class PagoComplementarioTests(BaseTope):
    def registrar(self, monto):
        with self.captureOnCommitCallbacks(execute=True):
            return servicios.registrar_pago_complementario(
                usuario=self.admin,
                destinatario=self.dest,
                mes=self.mes,
                monto=D(monto),
                metodo="transferencia",
                cuenta=self.cuenta,
            )

    def setUp(self):
        super().setUp()
        from datetime import date

        self.mes = date(2026, 10, 1)
        self.usado("420000.00")

    def test_registrar_que_cruza_avisa_sin_paciente(self):
        p = self.registrar("30000.00")
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(f"Pago complementario {p.pk}", mail.outbox[0].body)
        self.assertNotIn("Paciente", mail.outbox[0].body)

    def test_un_centavo_abajo_no_avisa(self):
        self.registrar("29999.99")
        self.assertEqual(mail.outbox, [])

    def test_editar_de_20000_a_30000_avisa(self):
        p = self.registrar("20000.00")
        with self.captureOnCommitCallbacks(execute=True):
            servicios.editar_pago_complementario(p, usuario=self.admin, monto=D("30000.00"))
        self.assertEqual(len(mail.outbox), 2)

    def test_editar_sin_cruzar_no_duplica_el_aviso_de_la_misma_cuenta_y_mes(self):
        p = self.registrar("30000.00")
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            servicios.editar_pago_complementario(p, usuario=self.admin, monto=D("40000.00"))
        self.assertEqual(mail.outbox, [])

    def test_cambio_a_un_mes_sin_cobros_no_avisa(self):
        from datetime import date

        p = self.registrar("20000.00")
        with self.captureOnCommitCallbacks(execute=True):
            servicios.editar_pago_complementario(
                p, usuario=self.admin, mes=date(2026, 9, 1), monto=D("30000.00")
            )
        self.assertEqual(mail.outbox, [])

    def test_anular_no_avisa(self):
        p = self.registrar("20000.00")
        with self.captureOnCommitCallbacks(execute=True):
            servicios.anular_pago_complementario(p, usuario=self.admin, motivo="datos_erroneos")
        self.assertEqual(mail.outbox, [])

    def test_pago_en_efectivo_sin_cuenta_no_avisa(self):
        with self.captureOnCommitCallbacks(execute=True):
            servicios.registrar_pago_complementario(
                usuario=self.admin,
                destinatario=self.dest,
                mes=self.mes,
                monto=D("90000.00"),
                metodo="efectivo",
            )
        self.assertEqual(mail.outbox, [])


class SeguroTests(BaseTope):
    def test_error_al_armar_el_aviso_no_deshace_el_cobro(self):
        from unittest import mock

        self.usado("420000.00")
        with mock.patch.object(avisos, "umbral", side_effect=RuntimeError("boom")):
            with self.assertLogs("sistema.avisos.servicios", "ERROR"):
                cobro = self.cobrar("30000.00")
        self.assertTrue(type(cobro).objects.filter(pk=cobro.pk).exists())
        self.assertEqual(mail.outbox, [])


class TopeDelMesCerradoTests(BaseTope):
    """FR-029: los avisos de un mes cerrado usan el tope de ese mes."""

    def setUp(self):
        super().setUp()
        self.cuenta.topes.update(desde=datetime(2026, 8, 1, tzinfo=ARG))
        editar_cuenta(self.cuenta, usuario=None, tope_mensual=D("300000.00"))
        self.sep = datetime(2026, 9, 15, 12, 0, tzinfo=ARG)

    def test_excedente_de_un_mes_cerrado_muestra_el_tope_de_ese_mes(self):
        cobro = self.usado("50000.00", fecha=self.sep, estado="finalizado")
        with self.captureOnCommitCallbacks(execute=True):
            avisos.excedente(cobro=cobro, usado=D("520000.00"))
        body = mail.outbox[0].body
        self.assertIn("Tope mensual: $500.000,00", body)
        self.assertIn("Excedente: $20.000,00", body)

    def test_cerca_del_tope_de_un_mes_cerrado_usa_el_tope_de_ese_mes(self):
        cobro = self.usado("30000.00", fecha=self.sep, estado="finalizado")
        for antes, despues, avisa in (
            ("449999.99", "450000.00", True),  # justo en el umbral del tope de septiembre
            ("440000.00", "449999.99", False),  # un centavo abajo
        ):
            mail.outbox.clear()
            with self.captureOnCommitCallbacks(execute=True):
                avisos.cerca_del_tope(
                    cuenta=self.cuenta,
                    momento=self.sep,
                    antes=D(antes),
                    despues=D(despues),
                    origen=cobro,
                )
            self.assertEqual(len(mail.outbox) > 0, avisa, despues)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            avisos.cerca_del_tope(
                cuenta=self.cuenta,
                momento=self.sep,
                antes=D("449999.99"),
                despues=D("450000.00"),
                origen=cobro,
            )
        self.assertIn("Tope mensual: $500.000,00", mail.outbox[0].body)
