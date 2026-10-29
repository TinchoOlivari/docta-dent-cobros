from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail

from sistema.asignacion.servicios import crear_cobro_pendiente
from sistema.avisos.models import ConfiguracionAvisos, DireccionAviso
from sistema.avisos.tests import AvisosTestCase
from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario, editar_cuenta

D = Decimal
AHORA = datetime(2026, 10, 20, 10, 0, tzinfo=ARG)
OCT = datetime(2026, 10, 5, 10, 0, tzinfo=ARG)
CBU = "0000000000000000000001"


class BaseTope(AvisosTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        for email in ("duenio@clinica.test", "admin@clinica.test"):
            DireccionAviso.objects.create(email=email)
        self.ana = get_user_model().objects.create_user(
            "ana", email="ana@example.com", role="operator"
        )
        self.admin = get_user_model().objects.create_user(
            "admin", email="admin@example.com", role="administrator"
        )
        self.dest = crear_destinatario(
            usuario=None, nombre="Destinatario Ejemplo", tipo="profesional"
        )
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="alias.uno",
            cbu=CBU,
            tope_mensual=D("500000.00"),
        )

    def usado(self, monto, fecha=OCT, estado="pendiente"):
        return cobro_prueba(
            cuenta=self.cuenta,
            destinatario=self.dest,
            monto=D(monto),
            fecha=fecha,
            estado=estado,
            creado_por=self.ana,
        )

    def cobrar(self, monto, usuario=None):
        with self.captureOnCommitCallbacks(execute=True):
            return crear_cobro_pendiente(
                monto=D(monto),
                usuario=usuario or self.ana,
                nombre_paciente="Juan Paciente",
                id_paciente_dentalink=501,
            )

    def asuntos(self):
        return [m.subject for m in mail.outbox]


class CercaDelTopeTests(BaseTope):
    def setUp(self):
        super().setUp()
        self.usado("420000.00")

    def test_cruza_el_umbral_avisa_a_cada_direccion(self):
        cobro = self.cobrar("30000.00")
        self.assertEqual(len(mail.outbox), 2)
        m = mail.outbox[0]
        self.assertEqual(m.subject, "Cuenta Destinatario Ejemplo — alias.uno cerca del tope")
        for texto in (
            "Destinatario Ejemplo",
            "$500.000,00",
            "$450.000,00",
            "Cupo: $50.000,00",
            f"Cobro {cobro.pk}",
            "Juan Paciente",
            "Mes: 10/2026",
            "http://localhost:8000/sistema/asignacion/",
        ):
            self.assertIn(texto, m.body)
        self.assertNotIn(CBU, m.body)

    def test_un_centavo_abajo_del_umbral_no_avisa(self):
        self.cobrar("29999.99")
        self.assertEqual(mail.outbox, [])

    def test_un_centavo_arriba_del_umbral_avisa(self):
        self.cobrar("30000.01")
        self.assertEqual(len(mail.outbox), 2)

    def test_ya_arriba_del_umbral_no_vuelve_a_avisar(self):
        self.usado("30000.00")
        self.cobrar("1.00")
        self.assertEqual(mail.outbox, [])

    def test_margen_cero_justo_en_el_tope_avisa(self):
        ConfiguracionAvisos.actual()
        ConfiguracionAvisos.objects.filter(pk=1).update(margen=0)
        self.cobrar("80000.00")
        self.assertEqual(len(mail.outbox), 2)

    def test_margen_cero_un_centavo_abajo_del_tope_no_avisa(self):
        ConfiguracionAvisos.actual()
        ConfiguracionAvisos.objects.filter(pk=1).update(margen=0)
        self.cobrar("79999.99")
        self.assertEqual(mail.outbox, [])

    def test_cobro_del_administrador_avisa_igual(self):
        self.cobrar("30000.00", usuario=self.admin)
        self.assertEqual(len(mail.outbox), 2)

    def test_cobro_de_noviembre_no_suma_a_octubre(self):
        self.usado("100000.00", fecha=datetime(2026, 11, 2, 10, 0, tzinfo=ARG))
        self.cobrar("29999.99")
        self.assertEqual(mail.outbox, [])

    def test_cancelar_baja_y_volver_a_cruzar_avisa_de_nuevo(self):
        primero = self.usado("30000.00")  # usado 450000: ya arriba
        servicios.cancelar_cobro(
            primero, usuario=self.ana, motivo="datos_erroneos", estado_visto="pendiente"
        )
        self.assertEqual(mail.outbox, [])
        self.cobrar("10000.00")  # 430000
        self.assertEqual(mail.outbox, [])
        self.cobrar("20000.00")  # 450000 justo
        self.assertEqual(len(mail.outbox), 2)

    def test_subir_el_tope_deja_la_cuenta_bajo_el_umbral_y_no_avisa(self):
        editar_cuenta(self.cuenta, usuario=None, tope_mensual=D("460000.00"))  # umbral 414000
        self.cobrar("1000.00")  # 421000: ya estaba arriba del umbral nuevo? no, 420000 > 414000
        self.assertEqual(mail.outbox, [])

    def test_adjuntar_comprobante_no_avisa(self):
        cobro = self.cobrar("1000.00")
        from sistema.cobros.tests.test_comprobante import imagen

        with mock.patch("sistema.dentalink.tasks.enviar_cobro.delay"):
            servicios.adjuntar_comprobante(cobro, imagen(), usuario=self.ana)
        self.assertEqual(mail.outbox, [])

    def test_cobro_con_estado_cancelado_no_cuenta(self):
        self.usado("100000.00", estado="cancelado")
        self.cobrar("29999.99")
        self.assertEqual(mail.outbox, [])
        self.assertEqual(Cobro.objects.filter(estado="pendiente").count(), 2)
