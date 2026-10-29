from sistema.arca.tests.fabricas import emisor_prueba
from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import ENTIDADES, foto, registrar_alta
from sistema.core.tests import SistemaTestCase


class AuditoriaEmisorTest(SistemaTestCase):
    def emisor_con_ticket(self):
        return emisor_prueba(ta_token="token-de-prueba", ta_sign="firma-de-prueba")

    def test_foto_no_tiene_secretos(self):
        datos = foto(self.emisor_con_ticket())
        for campo in ("certificado", "clave", "ta_token", "ta_sign", "ta_vence"):
            self.assertNotIn(campo, datos)
        self.assertIn("cuit", datos)

    def test_alta_de_emisor_no_guarda_pem_en_el_evento(self):
        evento = registrar_alta(self.emisor_con_ticket(), usuario=None)
        texto = str(Evento.objects.get(pk=evento.pk).cambios)
        for secreto in ("PRIVATE KEY", "BEGIN CERTIFICATE", "token-de-prueba", "firma-de-prueba"):
            self.assertNotIn(secreto, texto)
        self.assertIn("20000000001", texto)

    def test_entidades_tiene_emisor_y_comprobante(self):
        self.assertEqual(ENTIDADES["arca.emisor"], "Emisor")
        self.assertEqual(ENTIDADES["arca.comprobantefiscal"], "Comprobante fiscal")

    def test_tipo_emision_arca(self):
        self.assertEqual(Evento.Tipo.EMISION_ARCA.label, "Intento de emisión en ARCA")
