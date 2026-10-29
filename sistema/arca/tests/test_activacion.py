from django.urls import reverse

from payment_instructions.models import User
from sistema.arca.tests.fabricas import emisor_prueba
from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Destinatario


class ActivacionTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            "adm", email="adm@example.com", password="x", role="administrator"
        )
        self.client.force_login(self.admin)
        self.clinica = Destinatario.objects.create(nombre="Clínica", tipo="clinica")
        self.emisor = emisor_prueba()

    def guardar(self, destinatario=None, **campos):
        destinatario = destinatario or self.clinica
        datos = {"nombre": destinatario.nombre, "tipo": destinatario.tipo} | campos
        return self.client.post(reverse("destinatarios:editar", args=[destinatario.pk]), datos)

    def test_activar_con_emisor_guarda_y_deja_evento(self):
        r = self.guardar(facturacion_activa="on", emisor=self.emisor.pk)
        self.assertRedirects(r, reverse("destinatarios:detalle", args=[self.clinica.pk]))
        self.clinica.refresh_from_db()
        self.assertTrue(self.clinica.facturacion_activa)
        self.assertEqual(self.clinica.emisor, self.emisor)
        evento = Evento.objects.get(entidad="destinatarios.destinatario", tipo="cambio")
        nuevos = {c["campo"]: c["nuevo"] for c in evento.cambios}
        self.assertIs(nuevos["facturacion_activa"], True)
        self.assertEqual(nuevos["emisor_id"], self.emisor.pk)

    def test_activar_sin_emisor_da_error(self):
        r = self.guardar(facturacion_activa="on")
        self.assertContains(r, "Elegí un emisor para activar la facturación.")
        self.clinica.refresh_from_db()
        self.assertFalse(self.clinica.facturacion_activa)

    def test_activar_con_emisor_inactivo_da_error(self):
        inactivo = emisor_prueba(cuit="20000000002", estado="inactivo")
        r = self.guardar(facturacion_activa="on", emisor=inactivo.pk)
        self.assertContains(r, "Elegí un emisor Activo.")
        self.clinica.refresh_from_db()
        self.assertFalse(self.clinica.facturacion_activa)

    def test_dos_destinatarios_con_el_mismo_emisor_se_aceptan(self):
        otro = Destinatario.objects.create(nombre="Otra Clínica", tipo="clinica")
        self.guardar(facturacion_activa="on", emisor=self.emisor.pk)
        self.guardar(otro, facturacion_activa="on", emisor=self.emisor.pk)
        self.assertEqual(self.emisor.destinatarios.filter(facturacion_activa=True).count(), 2)

    def test_destinatario_con_emisor_que_pasó_a_inactivo_se_edita_sin_cambiar_el_emisor(self):
        self.guardar(facturacion_activa="on", emisor=self.emisor.pk)
        self.emisor.estado = "inactivo"
        self.emisor.save()
        r = self.guardar(facturacion_activa="on", emisor=self.emisor.pk, telefono="1155550000")
        self.assertRedirects(r, reverse("destinatarios:detalle", args=[self.clinica.pk]))

    def test_el_formulario_ofrece_solo_emisores_activos(self):
        emisor_prueba(cuit="20000000002", razon_social="Emisor Inactivo", estado="inactivo")
        r = self.client.get(reverse("destinatarios:editar", args=[self.clinica.pk]))
        self.assertContains(r, "Facturación activa")
        self.assertContains(r, self.emisor.razon_social)
        self.assertNotContains(r, "Emisor Inactivo")

    def test_desactivar_la_facturacion_conserva_el_emisor(self):
        self.guardar(facturacion_activa="on", emisor=self.emisor.pk)
        self.guardar(emisor=self.emisor.pk)
        self.clinica.refresh_from_db()
        self.assertFalse(self.clinica.facturacion_activa)
        self.assertEqual(self.clinica.emisor, self.emisor)
