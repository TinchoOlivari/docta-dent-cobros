from django.core.exceptions import ValidationError

from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Destinatario
from sistema.destinatarios.servicios import crear_destinatario, editar_destinatario


class DestinatarioTest(SistemaTestCase):
    def alta(self, **kw):
        datos = dict(nombre="Ana Test", tipo="profesional")
        datos.update(kw)
        return crear_destinatario(usuario=None, **datos)

    def test_alta_con_campos_de_fr001_nace_activo_con_fecha(self):
        d = self.alta(cuit="20000000001", email="a@example.com", telefono="1100000000")
        self.assertEqual(d.estado, "activo")
        self.assertIsNotNone(d.fecha_activacion)
        self.assertEqual(d.email, "a@example.com")

    def test_tipo_invalido_rechazado(self):
        with self.assertRaises(ValidationError):
            self.alta(tipo="otro")

    def test_cuit_de_10_y_12_digitos_y_con_letra_rechazado_11_ok(self):
        for cuit in ("1" * 10, "1" * 12, "2000000000a"):
            with self.assertRaises(ValidationError, msg=cuit):
                self.alta(cuit=cuit)
        self.assertEqual(self.alta(cuit="1" * 11).cuit, "1" * 11)

    def test_cuit_vacio_guarda_none_y_dos_sin_cuit_conviven(self):
        a = self.alta(cuit="")
        b = self.alta(nombre="Otro", cuit="")
        self.assertIsNone(a.cuit)
        self.assertIsNone(b.cuit)
        self.assertEqual(Destinatario.objects.count(), 2)

    def test_cuit_repetido_rechazado(self):
        self.alta(cuit="20000000001")
        with self.assertRaises(ValidationError) as cm:
            self.alta(nombre="Otro", cuit="20000000001")
        self.assertIn("Ya existe un destinatario con ese CUIT o CUIL.", cm.exception.messages)

    def test_editar_rechaza_estado(self):
        d = self.alta()
        with self.assertRaises(ValidationError):
            editar_destinatario(d, usuario=None, estado="archivado")

    def test_editar_cambia_nombre(self):
        d = self.alta()
        editar_destinatario(d, usuario=None, nombre="Nuevo")
        d.refresh_from_db()
        self.assertEqual(d.nombre, "Nuevo")
