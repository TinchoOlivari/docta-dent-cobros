import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError

from sistema.arca import cliente
from sistema.arca.cliente import leer_certificado, validar_clave
from sistema.arca.tests.fabricas import atrapar, certificado_prueba, fugas_en_traceback
from sistema.core.tests import SistemaTestCase


def inicio_del_certificado(cert: bytes):
    """`notBefore` leído con openssl: el test no depende del reloj."""
    salida = cliente._openssl("x509", "-noout", "-startdate", entrada=cert).stdout
    return cliente._vencimiento(salida.replace(b"notBefore", b"notAfter"))


class CertificadoTest(SistemaTestCase):
    def test_certificado_a_25_dias_vence_25_dias_despues_de_su_inicio(self):
        _, cert = certificado_prueba(25)
        vence = leer_certificado(cert)
        self.assertIsNotNone(vence.tzinfo)
        self.assertEqual(vence - inicio_del_certificado(cert), timedelta(days=25))

    def test_basura_da_certificado_invalido(self):
        with self.assertRaisesMessage(ValidationError, "El archivo no es un certificado válido."):
            leer_certificado(b"basura")

    def test_clave_de_prueba_pasa(self):
        clave, _ = certificado_prueba()
        self.assertIsNone(validar_clave(clave))

    def test_certificado_pasado_como_clave_da_clave_invalida(self):
        _, cert = certificado_prueba()
        with self.assertRaisesMessage(
            ValidationError, "El archivo no es una clave privada válida."
        ):
            validar_clave(cert)

    def test_clave_pasada_como_certificado_da_certificado_invalido(self):
        clave, _ = certificado_prueba()
        with self.assertRaises(ValidationError) as cm:
            leer_certificado(clave)
        self.assertEqual(cm.exception.messages, [cliente.CERTIFICADO_INVALIDO])

    def test_clave_cifrada_falla_sin_pedir_contraseña(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta, "clave.pem")
            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "RSA", "-aes256", "-pass", "pass:x"]
                + ["-out", str(ruta)],
                check=True,
                capture_output=True,
            )
            cifrada = ruta.read_bytes()
        with self.assertRaises(ValidationError):
            validar_clave(cifrada)

    def test_los_mensajes_de_error_no_llevan_el_pem(self):
        clave, cert = certificado_prueba()
        for funcion, pem in ((leer_certificado, clave), (validar_clave, cert)):
            with self.assertRaises(ValidationError) as cm:
                funcion(pem)
            self.assertNotIn("BEGIN", str(cm.exception))
            self.assertNotIn("PRIVATE", str(cm.exception))

    def test_el_traceback_del_error_no_lleva_el_pem_en_las_variables_locales(self):
        clave, cert = certificado_prueba()
        for funcion, pem in ((leer_certificado, clave), (validar_clave, cert)):
            error = atrapar(ValidationError, funcion, pem)
            self.assertEqual(fugas_en_traceback(error), [])

    def test_openssl_que_no_responde_da_error_de_validacion_y_no_lanza(self):
        clave, cert = certificado_prueba()
        for falla in (
            subprocess.TimeoutExpired("openssl", 10),
            FileNotFoundError("openssl"),
            subprocess.SubprocessError(),
        ):
            with mock.patch("subprocess.run", side_effect=falla):
                error = atrapar(ValidationError, leer_certificado, cert)
                self.assertEqual(fugas_en_traceback(error), [])
                with self.assertRaises(ValidationError):
                    validar_clave(clave)

    def test_control_del_detector_ve_el_pem_en_una_variable_local_de_produccion(self):
        _, cert = certificado_prueba()

        def falla(*args, **kwargs):  # no un Mock: sus marcos llevan `entrada` en kwargs
            raise RuntimeError

        with mock.patch.object(cliente, "_openssl", falla):
            error = atrapar(RuntimeError, leer_certificado, cert)
        self.assertEqual(fugas_en_traceback(error), ["leer_certificado.datos"])

    def test_control_del_detector_sin_la_fuga_no_encuentra_nada(self):
        clave, _ = certificado_prueba()
        error = atrapar(ValidationError, leer_certificado, clave)  # falla después del `del datos`
        self.assertEqual(fugas_en_traceback(error), [])

    def test_el_detector_falla_si_el_traceback_no_tiene_marcos_de_produccion(self):
        _, cert = certificado_prueba()
        with mock.patch.object(cliente, "_openssl", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError) as cm:
                leer_certificado(cert)  # assertRaises limpia el traceback
        with self.assertRaises(AssertionError):
            fugas_en_traceback(cm.exception)

    def test_openssl_se_llama_sin_shell_y_con_timeout(self):
        _, cert = certificado_prueba()
        with mock.patch("subprocess.run", wraps=subprocess.run) as run:
            leer_certificado(cert)
        self.assertEqual(run.call_args.args[0][0], "openssl")
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_fecha_con_dia_de_un_digito_se_lee(self):
        vence = cliente._vencimiento(b"notAfter=Oct  5 10:00:00 2027 GMT\n")
        self.assertEqual((vence.year, vence.month, vence.day, vence.hour), (2027, 10, 5, 10))

    def test_salida_ilegible_da_none(self):
        self.assertIsNone(cliente._vencimiento(b"cualquier cosa"))
