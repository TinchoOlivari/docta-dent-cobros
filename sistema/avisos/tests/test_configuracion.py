from datetime import datetime
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError

from sistema.auditoria.servicios import historial_de
from sistema.avisos.models import ConfiguracionAvisos, DireccionAviso
from sistema.avisos.servicios import (
    agregar_direccion,
    borrar_direccion,
    guardar_margen,
    margen,
)
from sistema.avisos.tests import AvisosTestCase
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

AHORA = datetime(2026, 10, 20, 10, 0, tzinfo=ARG)


class DireccionesTests(AvisosTestCase):
    def setUp(self):
        super().setUp()

    def test_dos_direcciones_quedan_con_evento_de_alta(self):
        a = agregar_direccion(usuario=None, email="duenio@clinica.test")
        b = agregar_direccion(usuario=None, email="admin@clinica.test")
        self.assertEqual(DireccionAviso.objects.count(), 2)
        for d in (a, b):
            self.assertEqual(historial_de(d).filter(tipo="alta").count(), 1)

    def test_email_sin_punto_en_el_dominio_se_rechaza(self):
        with self.assertRaises(ValidationError) as cm:
            agregar_direccion(usuario=None, email="duenio@clinica")
        self.assertEqual(cm.exception.messages, ["Escribí una dirección de email válida."])
        self.assertEqual(DireccionAviso.objects.count(), 0)

    def test_repetida_se_rechaza_y_queda_una_sola(self):
        agregar_direccion(usuario=None, email="admin@clinica.test")
        with self.assertRaises(ValidationError) as cm:
            agregar_direccion(usuario=None, email="admin@clinica.test")
        self.assertEqual(cm.exception.messages, ["La dirección ya está en la lista."])
        self.assertEqual(DireccionAviso.objects.count(), 1)

    def test_repetida_con_otra_capitalizacion_se_rechaza(self):
        agregar_direccion(usuario=None, email="admin@clinica.test")
        with self.assertRaises(ValidationError) as cm:
            agregar_direccion(usuario=None, email="Admin@clinica.test")
        self.assertEqual(cm.exception.messages, ["La dirección ya está en la lista."])

    def test_borrar_deja_evento_de_borrado_con_el_email(self):
        d = agregar_direccion(usuario=None, email="admin@clinica.test")
        borrar_direccion(d, usuario=None)
        self.assertEqual(DireccionAviso.objects.count(), 0)
        e = historial_de(d).get(tipo="borrado")
        self.assertIn(
            {"campo": "email", "anterior": "admin@clinica.test", "nuevo": None}, e.cambios
        )


class MargenTests(AvisosTestCase):
    def setUp(self):
        super().setUp()

    def test_cambiar_margen_deja_evento_10_a_20(self):
        conf = guardar_margen(usuario=None, margen=20)
        e = historial_de(conf).get(tipo="cambio")
        self.assertEqual(e.cambios, [{"campo": "margen", "anterior": 10, "nuevo": 20}])
        self.assertEqual(margen(), 20)

    def test_sin_cambio_no_deja_evento(self):
        conf = guardar_margen(usuario=None, margen=10)
        self.assertEqual(historial_de(conf).count(), 0)

    def test_margen_100_se_rechaza(self):
        with self.assertRaises(ValidationError) as cm:
            guardar_margen(usuario=None, margen=100)
        self.assertEqual(cm.exception.messages, ["El margen debe ser de 0 % a 99 %."])
        self.assertEqual(margen(), 10)

    def test_margen_99_y_0_pasan(self):
        guardar_margen(usuario=None, margen=99)
        self.assertEqual(margen(), 99)
        guardar_margen(usuario=None, margen=0)
        self.assertEqual(margen(), 0)
        self.assertEqual(ConfiguracionAvisos.objects.count(), 1)


class MargenNoAvisaTests(AvisosTestCase):
    def setUp(self):
        super().setUp()
        agregar_direccion(usuario=None, email="duenio@clinica.test")
        agregar_direccion(usuario=None, email="admin@clinica.test")
        dest = crear_destinatario(usuario=None, nombre="Destinatario Ejemplo", tipo="profesional")
        self.cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="alias.uno",
            tope_mensual=Decimal("500000.00"),
        )
        cobro_prueba(
            cuenta=self.cuenta, monto=Decimal("440000.00"), estado="finalizado", fecha=AHORA
        )

    def test_subir_el_margen_a_20_no_manda_emails(self):
        # 440000 pasa a estar sobre el umbral nuevo (400000), pero cambiar el margen no avisa.
        with self.captureOnCommitCallbacks(execute=True):
            guardar_margen(usuario=None, margen=20)
        self.assertEqual(len(mail.outbox), 0)
