from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from sistema.arca.tests.fabricas import emisor_prueba
from sistema.auditoria.servicios import foto
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Destinatario


class FacturacionTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()

    def destinatario(self, **campos):
        datos = dict(nombre="Clínica Prueba", tipo="clinica")
        return Destinatario.objects.create(**(datos | campos))

    def test_facturacion_sin_emisor_salta_la_constraint(self):
        with self.assertRaises(IntegrityError) as cm, transaction.atomic():
            self.destinatario(facturacion_activa=True)
        self.assertIn("destinatario_facturacion_con_emisor", str(cm.exception))

    def test_emisor_sin_facturacion_activa_entra(self):
        self.destinatario(emisor=self.emisor)

    def test_full_clean_activar_con_emisor_inactivo_da_error(self):
        self.emisor.estado = "inactivo"
        self.emisor.save()
        d = self.destinatario()
        d.facturacion_activa, d.emisor = True, self.emisor
        with self.assertRaises(ValidationError) as cm:
            d.full_clean()
        self.assertEqual(cm.exception.message_dict["emisor"], ["Elegí un emisor Activo."])

    def test_full_clean_activar_con_emisor_activo_pasa(self):
        d = self.destinatario()
        d.facturacion_activa, d.emisor = True, self.emisor
        d.full_clean()

    def test_emisor_que_pasa_a_inactivo_no_frena_ediciones_sin_cambio_de_facturacion(self):
        d = self.destinatario(facturacion_activa=True, emisor=self.emisor)
        self.emisor.estado = "inactivo"
        self.emisor.save()
        d.refresh_from_db()
        d.nombre = "Otro nombre"
        d.full_clean()

    def test_cambiar_a_otro_emisor_inactivo_da_error(self):
        d = self.destinatario(facturacion_activa=True, emisor=self.emisor)
        otro = emisor_prueba(cuit="20000000002", estado="inactivo")
        d.emisor = otro
        with self.assertRaises(ValidationError):
            d.full_clean()

    def test_foto_incluye_facturacion_activa_y_emisor_id(self):
        d = self.destinatario(facturacion_activa=True, emisor=self.emisor)
        f = foto(d)
        self.assertIs(f["facturacion_activa"], True)
        self.assertEqual(f["emisor_id"], self.emisor.pk)
