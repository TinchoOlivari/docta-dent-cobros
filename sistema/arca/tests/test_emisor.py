from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import DataError
from django.core.files.uploadedfile import SimpleUploadedFile

from sistema.arca import servicios
from sistema.arca.models import Emisor
from sistema.arca.tests.fabricas import (
    AHORA,
    certificado_prueba,
    emisor_prueba,
    atrapar,
    fugas_en_traceback,
)
from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase

DATOS = dict(
    cuit="20000000001",
    razon_social="Clínica Prueba S.A.S.",
    condicion_iva="responsable_inscripto",
    punto_venta=3,
    tipo_comprobante="b",
    alicuota_iva="21",
)
VENCE = AHORA + timedelta(days=365)


def archivos(dias=365):
    clave, cert = certificado_prueba(dias)
    return dict(
        certificado=SimpleUploadedFile("cert.pem", cert),
        clave=SimpleUploadedFile("clave.key", clave),
    )


class EmisorServiciosTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)

    def crear(self, **extra):
        # El vencimiento sale del reloj fijo, no del certificado real.
        with mock.patch("sistema.arca.servicios.leer_certificado", return_value=VENCE):
            return servicios.crear_emisor(usuario=None, **DATOS, **archivos(), **extra)

    def eventos(self, emisor, tipo):
        return Evento.objects.filter(entidad="arca.emisor", entidad_id=emisor.pk, tipo=tipo)

    def test_alta_con_certificado_y_clave_guarda_vencimiento_y_evento(self):
        emisor = self.crear()
        emisor.refresh_from_db()
        self.assertEqual(emisor.certificado_vence, VENCE)
        self.assertEqual(emisor.fecha_activacion, AHORA)
        self.assertIn("BEGIN CERTIFICATE", emisor.certificado)
        self.assertIn("PRIVATE KEY", emisor.clave)
        evento = self.eventos(emisor, "alta").get()
        self.assertNotIn("BEGIN", str(evento.cambios))
        self.assertNotIn("PRIVATE", str(evento.cambios))

    def test_alta_sin_certificado_se_guarda(self):
        emisor = servicios.crear_emisor(usuario=None, **DATOS)
        self.assertIsNone(emisor.certificado_vence)
        self.assertEqual(emisor.certificado, "")

    def test_alta_con_archivo_basura_da_error_y_no_guarda(self):
        malos = dict(
            certificado=SimpleUploadedFile("c.pem", b"basura"),
            clave=SimpleUploadedFile("k.pem", b"basura"),
        )
        with self.assertRaisesMessage(ValidationError, "El archivo no es un certificado válido."):
            servicios.crear_emisor(usuario=None, **DATOS, **malos)
        self.assertFalse(Emisor.objects.exists())

    def test_alta_con_la_clave_en_el_lugar_del_certificado_no_filtra_el_pem(self):
        clave, cert = certificado_prueba()
        cambiados = dict(
            certificado=SimpleUploadedFile("c.pem", clave),
            clave=SimpleUploadedFile("k.pem", cert),
        )
        error = atrapar(ValidationError, servicios.crear_emisor, usuario=None, **DATOS, **cambiados)
        self.assertEqual(fugas_en_traceback(error), [])
        self.assertNotIn("PRIVATE", str(error))

    def test_alta_con_clave_invalida_no_filtra_el_pem(self):
        _, cert = certificado_prueba()
        datos = dict(
            certificado=SimpleUploadedFile("c.pem", cert),
            clave=SimpleUploadedFile("k.pem", cert),
        )
        with self.assertRaisesMessage(
            ValidationError, "El archivo no es una clave privada válida."
        ):
            servicios.crear_emisor(usuario=None, **DATOS, **datos)

    def test_cuit_repetido_da_error(self):
        self.crear()
        with self.assertRaisesMessage(ValidationError, "Ya existe un emisor con ese CUIT."):
            servicios.crear_emisor(usuario=None, **DATOS)

    def test_edicion_con_certificado_nuevo_borra_el_ticket_y_el_aviso(self):
        emisor = emisor_prueba(
            ta_token="t", ta_sign="s", ta_vence=AHORA, aviso_certificado=AHORA - timedelta(days=1)
        )
        nuevo = AHORA + timedelta(days=500)
        _, cert = certificado_prueba()
        with mock.patch("sistema.arca.servicios.leer_certificado", return_value=nuevo):
            servicios.editar_emisor(
                emisor, usuario=None, certificado=SimpleUploadedFile("c.pem", cert)
            )
        emisor.refresh_from_db()
        self.assertEqual((emisor.ta_token, emisor.ta_sign, emisor.ta_vence), ("", "", None))
        self.assertIsNone(emisor.aviso_certificado)
        self.assertEqual(emisor.certificado_vence, nuevo)
        evento = self.eventos(emisor, "cambio").get()
        self.assertEqual(evento.detalle, "Certificado reemplazado.")
        self.assertNotIn("BEGIN", str(evento.cambios))

    def test_edicion_con_clave_invalida_no_cambia_nada(self):
        emisor = emisor_prueba()
        antes = emisor.clave
        with self.assertRaises(ValidationError):
            servicios.editar_emisor(
                emisor, usuario=None, clave=SimpleUploadedFile("k.pem", b"basura")
            )
        emisor.refresh_from_db()
        self.assertEqual(emisor.clave, antes)

    def test_edicion_sin_cambios_no_crea_evento(self):
        emisor = emisor_prueba()
        servicios.editar_emisor(emisor, usuario=None, razon_social=emisor.razon_social)
        self.assertFalse(Evento.objects.filter(entidad="arca.emisor").exists())

    def test_edicion_de_dato_deja_evento_con_el_cambio(self):
        emisor = emisor_prueba()
        servicios.editar_emisor(emisor, usuario=None, razon_social="Otra S.A.")
        evento = self.eventos(emisor, "cambio").get()
        self.assertEqual([c["campo"] for c in evento.cambios], ["razon_social"])

    def test_edicion_con_estado_da_error(self):
        emisor = emisor_prueba()
        with self.assertRaisesMessage(
            ValidationError, "El estado se cambia con cambiar_estado_emisor."
        ):
            servicios.editar_emisor(emisor, usuario=None, estado="inactivo")

    def test_desactivar_pone_fecha_archivado_y_deja_evento(self):
        emisor = emisor_prueba()
        servicios.cambiar_estado_emisor(emisor, usuario=None, estado="inactivo")
        emisor.refresh_from_db()
        self.assertEqual((emisor.estado, emisor.fecha_archivado), ("inactivo", AHORA))
        evento = self.eventos(emisor, "cambio").get()
        self.assertIn("estado", [c["campo"] for c in evento.cambios])

    def test_desactivar_dos_veces_da_error(self):
        emisor = emisor_prueba()
        servicios.cambiar_estado_emisor(emisor, usuario=None, estado="inactivo")
        with self.assertRaisesMessage(ValidationError, "El emisor ya está Inactivo."):
            servicios.cambiar_estado_emisor(emisor, usuario=None, estado="inactivo")
        self.assertEqual(self.eventos(emisor, "cambio").count(), 1)

    def falla_de_base(self, accion):
        """La excepción con sus marcos vivos: `assertRaises` los limpia y el chequeo no vería nada."""
        with mock.patch(
            "django.db.models.sql.compiler.SQLUpdateCompiler.execute_sql",
            side_effect=DataError("caída"),
        ):
            try:
                accion()
            except DataError as error:
                return error
        self.fail("La falla de base no llegó")

    def test_falla_de_base_al_cambiar_estado_no_lleva_la_clave_ni_el_certificado(self):
        emisor = emisor_prueba()
        error = self.falla_de_base(
            lambda: servicios.cambiar_estado_emisor(emisor, usuario=None, estado="inactivo")
        )
        self.assertEqual(fugas_en_traceback(error), [])

    def test_falla_de_base_al_editar_un_dato_no_lleva_la_clave_ni_el_certificado(self):
        emisor = emisor_prueba()
        error = self.falla_de_base(
            lambda: servicios.editar_emisor(emisor, usuario=None, razon_social="Otra S.A.")
        )
        self.assertEqual(fugas_en_traceback(error), [])

    def test_cambiar_estado_y_editar_un_dato_no_pisan_la_clave_guardada(self):
        emisor = emisor_prueba()
        clave, cert = emisor.clave, emisor.certificado
        servicios.cambiar_estado_emisor(emisor, usuario=None, estado="inactivo")
        servicios.editar_emisor(emisor, usuario=None, razon_social="Otra S.A.")
        emisor.refresh_from_db()
        self.assertEqual((emisor.clave, emisor.certificado), (clave, cert))
        self.assertEqual((emisor.estado, emisor.razon_social), ("inactivo", "Otra S.A."))

    def test_activar_pone_fecha_activacion(self):
        emisor = emisor_prueba(estado="inactivo", fecha_activacion=None)
        servicios.cambiar_estado_emisor(emisor, usuario=None, estado="activo")
        emisor.refresh_from_db()
        self.assertEqual((emisor.estado, emisor.fecha_activacion), ("activo", AHORA))

    def test_estado_inexistente_da_error(self):
        emisor = emisor_prueba()
        with self.assertRaises(ValidationError):
            servicios.cambiar_estado_emisor(emisor, usuario=None, estado="otro")
