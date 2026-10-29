import subprocess
import tempfile
import traceback
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

from sistema.arca.cliente import Resultado
from sistema.arca.models import ComprobanteFiscal, Emisor
from sistema.cobros.models import Cobro
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta, Destinatario

AHORA = datetime(2026, 10, 20, 10, 0, tzinfo=ARG)
_certificados = {}


def certificado_prueba(dias=365) -> tuple[bytes, bytes]:
    """Clave y certificado PEM de prueba, generados con openssl. Cache por `dias`."""
    if dias not in _certificados:
        with tempfile.TemporaryDirectory() as carpeta:
            clave, cert = Path(carpeta, "clave.pem"), Path(carpeta, "cert.pem")
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", str(dias)]
                + ["-subj", "/CN=prueba", "-keyout", str(clave), "-out", str(cert)],
                check=True,
                capture_output=True,
            )
            _certificados[dias] = (clave.read_bytes(), cert.read_bytes())
    return _certificados[dias]


def emisor_prueba(**campos) -> Emisor:
    clave, cert = certificado_prueba()
    datos = dict(
        cuit="20000000001",
        razon_social="Clínica Prueba S.A.S.",
        condicion_iva="responsable_inscripto",
        punto_venta=3,
        tipo_comprobante="b",
        alicuota_iva="21",
        certificado=cert.decode(),
        clave=clave.decode(),
        certificado_vence=AHORA + timedelta(days=365),
    )
    return Emisor.objects.create(**(datos | campos))


def cobro_facturable(**campos) -> Cobro:
    """Cobro en efectivo Finalizado, sin pasar por los servicios de 007."""
    cuenta = Cuenta.objects.filter(tipo="efectivo").first()
    if cuenta is None:  # hay una sola cuenta de efectivo activa
        destinatario = Destinatario.objects.create(nombre="Clínica Prueba", tipo="clinica")
        cuenta = Cuenta.objects.create(destinatario=destinatario, tipo="efectivo")
    datos = dict(
        cuenta=cuenta, monto="65000.00", metodo="efectivo", estado="finalizado", fecha=AHORA
    )
    return Cobro.objects.create(**(datos | campos))


def factura_prueba(cobro, **campos) -> ComprobanteFiscal:
    datos = dict(
        cobro=cobro,
        emisor=campos.pop("emisor", None) or Emisor.objects.first() or emisor_prueba(),
        tipo="factura",
        concepto="servicios",
        descripcion="Servicios odontológicos",
    )
    return ComprobanteFiscal.objects.create(**(datos | campos))


def _lleva_pem(valor, nivel=0) -> bool:
    if isinstance(valor, bytes):
        valor = valor.decode(errors="replace")
    if isinstance(valor, str):
        return "PRIVATE KEY" in valor or "BEGIN CERT" in valor
    if nivel < 4 and isinstance(valor, dict):
        valor = [*valor.keys(), *valor.values()]
    if nivel < 4 and isinstance(valor, (list, tuple, set)):
        return any(_lleva_pem(v, nivel + 1) for v in valor)
    return False


def atrapar(tipo, funcion, *args, **kwargs):
    """La excepción con sus marcos vivos. `assertRaises` los limpia y el detector no vería nada."""
    try:
        funcion(*args, **kwargs)
    except tipo as error:
        return error
    raise AssertionError(f"No se lanzó {tipo.__name__}")


def fugas_en_traceback(excepcion) -> list[str]:
    """Variables locales del código de producción en el traceback que llevan un PEM (R3).

    Falla si el traceback no tiene ningún marco de producción: no vería nada (T114).
    """
    fugas = []
    vistos = 0
    for marco, _ in traceback.walk_tb(excepcion.__traceback__):
        if "/tests/" in marco.f_code.co_filename:
            continue
        vistos += 1
        for nombre, valor in marco.f_locals.items():
            if _lleva_pem(valor):
                fugas.append(f"{marco.f_code.co_name}.{nombre}")
    if not vistos:
        raise AssertionError("El traceback no tiene marcos de producción: el detector no ve nada.")
    return fugas


CAE = "75123456789012"


class ArcaFalso:
    """Doble de las 4 llamadas de red de `sistema.arca.cliente`. ARCA autoriza y suma 1 al último."""

    def __init__(self, test, ultimo=41):
        self.ultimo = ultimo
        self.login = mock.Mock(
            return_value=Resultado(
                "ok",
                {
                    "token": "TOKEN-DE-PRUEBA",
                    "sign": "SIGN-DE-PRUEBA",
                    "vence": AHORA + timedelta(hours=12),
                },
            )
        )
        self.ultimo_autorizado = mock.Mock(
            side_effect=lambda *a: Resultado("ok", {"numero": self.ultimo})
        )
        self.solicitar_cae = mock.Mock(side_effect=self._autorizar)
        self.consultar = mock.Mock(return_value=Resultado("rechazado", error="602: no existe"))
        for nombre in ("login", "ultimo_autorizado", "solicitar_cae", "consultar"):
            parche = mock.patch(f"sistema.arca.cliente.{nombre}", getattr(self, nombre))
            parche.start()
            test.addCleanup(parche.stop)

    def responder(self, *resultados):
        """`solicitar_cae` devuelve estos resultados en orden y después autoriza."""
        pendientes = list(resultados)
        self.solicitar_cae.side_effect = lambda *a: (
            pendientes.pop(0) if pendientes else self._autorizar(*a)
        )

    def _autorizar(self, emisor, ta, pedido):
        self.ultimo = max(self.ultimo, pedido["numero"])
        return Resultado("ok", {"cae": CAE, "cae_vence": date(2026, 10, 30)})

    def no_llamo_a_arca(self):
        return not (self.login.called or self.ultimo_autorizado.called or self.solicitar_cae.called)


def datos_de_arca(**campos) -> Resultado:
    """Lo que devuelve `consultar` cuando ARCA tiene el comprobante."""
    datos = dict(
        cae=CAE,
        cae_vence=date(2026, 10, 30),
        fecha=date(2026, 10, 20),
        doc_tipo=96,
        doc_numero="30111222",
        total=Decimal("65000.00"),
    )
    return Resultado("ok", datos | campos)
