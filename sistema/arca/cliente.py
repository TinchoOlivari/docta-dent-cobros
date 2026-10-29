"""Único lugar que habla con ARCA y que llama a `openssl` (research R3)."""

import base64
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.exceptions import ValidationError

from sistema.core.tiempo import ARG

CERTIFICADO_INVALIDO = "El archivo no es un certificado válido."
CLAVE_INVALIDA = "El archivo no es una clave privada válida."


def _openssl(*args, entrada: bytes) -> subprocess.CompletedProcess:
    """Nunca lanza: así ningún traceback con la clave en `entrada` llega a Sentry (R3)."""
    try:
        return subprocess.run(
            ["openssl", *args], input=entrada, capture_output=True, timeout=10, check=False
        )
    except (subprocess.SubprocessError, OSError):
        return subprocess.CompletedProcess(args, -1, b"", b"")


def _vencimiento(salida: bytes) -> datetime | None:
    # Formato de openssl: `notAfter=Oct  5 10:00:00 2027 GMT`. Los meses son en inglés.
    linea = " ".join(salida.decode("ascii", errors="replace").split())
    try:
        return datetime.strptime(linea, "notAfter=%b %d %H:%M:%S %Y GMT").replace(tzinfo=UTC)
    except ValueError:
        return None


def leer_certificado(datos: bytes) -> datetime:
    """Vencimiento (aware, UTC) del certificado PEM. Nunca pone `datos` ni `stderr` en el error."""
    resultado = _openssl("x509", "-noout", "-enddate", entrada=datos)
    del datos  # el traceback de un error no debe llevar el PEM en las variables locales
    vence = _vencimiento(resultado.stdout) if resultado.returncode == 0 else None
    if vence is None:
        raise ValidationError(CERTIFICADO_INVALIDO)
    return vence


def validar_clave(datos: bytes) -> None:
    # `-passin pass:`: una clave cifrada falla en vez de pedir la contraseña por terminal.
    resultado = _openssl("pkey", "-noout", "-passin", "pass:", entrada=datos)
    del datos
    if resultado.returncode != 0:
        raise ValidationError(CLAVE_INVALIDA)


# --- Red: WSAA y WSFEv1 (research R1, R4, R5, R7) ---

URL_WSAA = {
    "prueba": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
    "produccion": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
}
URL_WSFE = {
    "prueba": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    "produccion": "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
}
NS_WSFE = "http://ar.gov.afip.dif.FEV1/"
NO_FIRMADO = "No se pudo firmar el pedido a ARCA."
SIN_PRODUCCION = "Este servidor no puede usar el ambiente de producción de ARCA."
MAX_ERROR = 500
MAX_RESPUESTA = 2_000_000
CENTAVO = Decimal("0.01")
# ARCA: 600 a 602 son errores de token. En `consultar`, 602 es "no existe el comprobante".
ERRORES_TOKEN = {"600", "601", "602"}


@dataclass(frozen=True)
class Resultado:
    """`tipo`: ok, rechazado, reintentable o dudoso (R7). `datos` nunca sale en el repr (token)."""

    tipo: str
    datos: object = field(default=None, repr=False)
    error: str = ""

    def __post_init__(self):
        object.__setattr__(self, "error", self.error[:MAX_ERROR])


def _url(tabla: dict, emisor) -> str | None:
    """None si es producción y este servidor no la permite (R5, FR-032)."""
    if emisor.ambiente == "produccion" and not settings.ARCA_PRODUCCION:
        return None
    return tabla[emisor.ambiente]


def _xml(datos: bytes) -> ET.Element | None:
    """Raíz sin espacios de nombres. None si no es XML. Rechaza DTD: sin entidades externas."""
    if b"<!DOCTYPE" in datos or b"<!ENTITY" in datos:
        return None
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError:
        return None
    for elemento in raiz.iter():
        elemento.tag = elemento.tag.rpartition("}")[2]
    return raiz


def _texto(raiz: ET.Element, ruta: str) -> str:
    elemento = raiz.find(ruta)
    return (elemento.text or "").strip() if elemento is not None else ""


def _soap(url: str, accion: str, cuerpo: str, *, escritura: bool = False) -> Resultado:
    """Un POST SOAP. Nunca lanza. `ok` lleva la raíz del sobre en `datos`.

    Falla al conectar: nada salió, `reintentable`. Falla después de enviar: ARCA pudo procesar el
    pedido. En una operación de `escritura` (FECAESolicitar) eso es `dudoso`, igual que una
    respuesta ilegible; en una lectura repetirla no cuesta nada, `reintentable`.
    """
    partes = urlsplit(url)
    ambiguo = "dudoso" if escritura else "reintentable"
    # TODO(spec-pendiente: confirmar contra el ambiente de prueba de ARCA, con el certificado
    # de Martin, URL, SOAPAction, orden de campos y el TLS de wsaahomo)
    conexion = HTTPSConnection(partes.netloc, timeout=5)
    try:
        try:
            conexion.connect()
        except (OSError, HTTPException) as e:
            return Resultado(
                "reintentable", error=f"No se pudo conectar con ARCA ({type(e).__name__})."
            )
        try:
            conexion.sock.settimeout(30)
            conexion.request(
                "POST",
                partes.path,
                cuerpo.encode("utf-8"),
                {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": accion},
            )
            respuesta = conexion.getresponse()
            status, datos = respuesta.status, respuesta.read(MAX_RESPUESTA)
        except (OSError, HTTPException) as e:
            return Resultado("dudoso", error=f"ARCA no respondió a tiempo ({type(e).__name__}).")
    finally:
        conexion.close()
    raiz = _xml(datos)
    if raiz is not None and raiz.tag == "Envelope":
        falla = raiz.find(".//Fault")
        if falla is not None:
            return Resultado("reintentable", error=_texto(falla, "faultstring") or "Fault de ARCA.")
        if status == 200:
            return Resultado("ok", raiz)
    if status >= 500 or status == 200:
        return Resultado(ambiguo, error=f"Respuesta ilegible de ARCA (HTTP {status}).")
    return Resultado("reintentable", error=f"Respuesta inesperada de ARCA (HTTP {status}).")


def _lectura(resultado: Resultado) -> Resultado:
    """Una lectura que se cortó se repite sin riesgo: nunca es `dudoso`."""
    if resultado.tipo == "dudoso":
        return Resultado("reintentable", error=resultado.error)
    return resultado


def _escribir_pem(ruta: Path, emisor, campo: str) -> None:
    """Escribe `emisor.<campo>` con 0600. El PEM no pasa por una variable local (R3)."""
    descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as archivo:
        archivo.write(getattr(emisor, campo).encode())


def firmar_tra(emisor, *, ahora: datetime) -> str:
    """TRA para `wsfe` firmado en CMS (DER), en base64. Falla: `ValueError` sin PEM ni stderr."""
    local = ahora.astimezone(ARG)
    tra = (
        '<?xml version="1.0" encoding="UTF-8"?><loginTicketRequest version="1.0"><header>'
        f"<uniqueId>{int(ahora.timestamp())}</uniqueId>"
        f"<generationTime>{(local - timedelta(minutes=10)).isoformat(timespec='seconds')}"
        "</generationTime>"
        f"<expirationTime>{(local + timedelta(minutes=10)).isoformat(timespec='seconds')}"
        "</expirationTime></header><service>wsfe</service></loginTicketRequest>"
    )
    try:
        with tempfile.TemporaryDirectory() as carpeta:
            cert, clave = Path(carpeta, "cert.pem"), Path(carpeta, "clave.pem")
            _escribir_pem(cert, emisor, "certificado")
            _escribir_pem(clave, emisor, "clave")
            resultado = _openssl(
                *("cms", "-sign", "-signer", str(cert), "-inkey", str(clave)),
                *("-nodetach", "-outform", "DER"),
                entrada=tra.encode(),
            )
    except OSError:
        raise ValueError(NO_FIRMADO) from None
    if resultado.returncode != 0 or not resultado.stdout:
        raise ValueError(NO_FIRMADO)
    return base64.b64encode(resultado.stdout).decode()


def login(emisor, *, ahora: datetime) -> Resultado:
    """`loginCms`. `datos = {"token", "sign", "vence"}`. Cualquier Fault es `reintentable`."""
    url = _url(URL_WSAA, emisor)
    if url is None:
        return Resultado("rechazado", error=SIN_PRODUCCION)
    try:
        cms = firmar_tra(emisor, ahora=ahora)
    except ValueError:
        return Resultado("rechazado", error=NO_FIRMADO)
    cuerpo = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:wsaa="http://wsaa.view.sua.dvadac.desein.afip.gov"><soapenv:Header/><soapenv:Body>'
        f"<wsaa:loginCms><wsaa:in0>{escape(cms)}</wsaa:in0></wsaa:loginCms>"
        "</soapenv:Body></soapenv:Envelope>"
    )
    resultado = _lectura(_soap(url, "", cuerpo))
    if resultado.tipo != "ok":
        return resultado
    ticket = _xml(_texto(resultado.datos, ".//loginCmsReturn").encode())
    token, sign = (
        (_texto(ticket, f".//{n}") for n in ("token", "sign")) if ticket is not None else ("", "")
    )
    try:
        vence = datetime.fromisoformat(_texto(ticket, ".//expirationTime")) if token else None
    except ValueError:
        vence = None
    if not (token and sign and vence):
        return Resultado("reintentable", error="ARCA devolvió un ticket de acceso ilegible.")
    if vence.tzinfo is None:
        vence = vence.replace(tzinfo=ARG)
    return Resultado("ok", {"token": token, "sign": sign, "vence": vence})


def _errores(raiz: ET.Element) -> str:
    """ "código: mensaje" de `Errors/Err` y `Observaciones/Obs`, separados por "; "."""
    filas = [*raiz.iterfind(".//Errors/Err"), *raiz.iterfind(".//Observaciones/Obs")]
    return "; ".join(f"{_texto(f, 'Code')}: {_texto(f, 'Msg')}" for f in filas)


def _codigos_error(raiz: ET.Element) -> set[str]:
    return {_texto(e, "Code") for e in raiz.iterfind(".//Errors/Err")}


def _wsfe(emisor, ta: dict, operacion: str, campos: str, *, escritura: bool = False) -> Resultado:
    url = _url(URL_WSFE, emisor)
    if url is None:
        return Resultado("rechazado", error=SIN_PRODUCCION)
    cuerpo = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:ar="{NS_WSFE}"><soapenv:Header/><soapenv:Body><ar:{operacion}><ar:Auth>'
        f"<ar:Token>{escape(ta['token'])}</ar:Token><ar:Sign>{escape(ta['sign'])}</ar:Sign>"
        f"<ar:Cuit>{escape(emisor.cuit)}</ar:Cuit></ar:Auth>{campos}</ar:{operacion}>"
        "</soapenv:Body></soapenv:Envelope>"
    )
    resultado = _soap(url, NS_WSFE + operacion, cuerpo, escritura=escritura)
    return resultado if escritura else _lectura(resultado)


def _con_errores(raiz: ET.Element, tokens=ERRORES_TOKEN) -> Resultado:
    """Respuesta sin dato útil con `Errors`: reintentable; los de token piden ticket nuevo (R4)."""
    token_invalido = bool(_codigos_error(raiz) & tokens)
    return Resultado(
        "reintentable",
        {"token_invalido": True} if token_invalido else None,
        _errores(raiz) or "Respuesta sin datos de ARCA.",
    )


def ultimo_autorizado(emisor, ta: dict, cbte_tipo: int) -> Resultado:
    """`FECompUltimoAutorizado`. `datos = {"numero": int}`."""
    campos = f"<ar:PtoVta>{int(emisor.punto_venta)}</ar:PtoVta><ar:CbteTipo>{int(cbte_tipo)}</ar:CbteTipo>"
    resultado = _wsfe(emisor, ta, "FECompUltimoAutorizado", campos)
    if resultado.tipo != "ok":
        return resultado
    numero = _texto(resultado.datos, ".//FECompUltimoAutorizadoResult/CbteNro")
    if not numero.isdigit():
        return _con_errores(resultado.datos)
    return Resultado("ok", {"numero": int(numero)})


def _importe(monto) -> str:
    """Decimal con a lo sumo 2 decimales, escrito sin pasar por float."""
    if not isinstance(monto, Decimal) or monto != monto.quantize(CENTAVO):
        raise ValueError("El importe debe ser un Decimal con 2 decimales como máximo.")
    return f"{monto:.2f}"


def _fecha(valor: date) -> str:
    return valor.strftime("%Y%m%d")


def _detalle(pedido: dict) -> str:
    concepto = int(pedido["concepto"])
    if concepto not in (1, 2, 3):
        raise ValueError("Concepto de ARCA inválido.")
    partes = [
        f"<ar:Concepto>{concepto}</ar:Concepto>",
        f"<ar:DocTipo>{int(pedido['doc_tipo'])}</ar:DocTipo>",
        f"<ar:DocNro>{int(pedido['doc_numero'])}</ar:DocNro>",
        f"<ar:CbteDesde>{int(pedido['numero'])}</ar:CbteDesde>",
        f"<ar:CbteHasta>{int(pedido['numero'])}</ar:CbteHasta>",
        f"<ar:CbteFch>{_fecha(pedido['fecha'])}</ar:CbteFch>",
        f"<ar:ImpTotal>{_importe(pedido['total'])}</ar:ImpTotal>",
        "<ar:ImpTotConc>0.00</ar:ImpTotConc>",
        f"<ar:ImpNeto>{_importe(pedido['neto'])}</ar:ImpNeto>",
        "<ar:ImpOpEx>0.00</ar:ImpOpEx>",
        f"<ar:ImpIVA>{_importe(pedido['iva'])}</ar:ImpIVA>",
        "<ar:ImpTrib>0.00</ar:ImpTrib>",
    ]
    if concepto in (2, 3):
        partes += [
            f"<ar:FchServDesde>{_fecha(pedido['servicio_desde'])}</ar:FchServDesde>",
            f"<ar:FchServHasta>{_fecha(pedido['servicio_hasta'])}</ar:FchServHasta>",
            f"<ar:FchVtoPago>{_fecha(pedido['vto_pago'])}</ar:FchVtoPago>",
        ]
    partes += [
        "<ar:MonId>PES</ar:MonId><ar:MonCotiz>1</ar:MonCotiz>",
        "<ar:CondicionIVAReceptorId>5</ar:CondicionIVAReceptorId>",
    ]
    if pedido.get("asociado"):
        a = pedido["asociado"]
        partes.append(
            f"<ar:CbtesAsoc><ar:CbteAsoc><ar:Tipo>{int(a['tipo'])}</ar:Tipo>"
            f"<ar:PtoVta>{int(a['punto_venta'])}</ar:PtoVta><ar:Nro>{int(a['numero'])}</ar:Nro>"
            f"<ar:Cuit>{int(a['cuit'])}</ar:Cuit><ar:CbteFch>{_fecha(a['fecha'])}</ar:CbteFch>"
            "</ar:CbteAsoc></ar:CbtesAsoc>"
        )
    if pedido.get("alicuota_id"):
        partes.append(
            f"<ar:Iva><ar:AlicIva><ar:Id>{int(pedido['alicuota_id'])}</ar:Id>"
            f"<ar:BaseImp>{_importe(pedido['neto'])}</ar:BaseImp>"
            f"<ar:Importe>{_importe(pedido['iva'])}</ar:Importe></ar:AlicIva></ar:Iva>"
        )
    return "".join(partes)


def solicitar_cae(emisor, ta: dict, pedido: dict) -> Resultado:
    """`FECAESolicitar` de 1 comprobante. `datos = {"cae", "cae_vence"}`.

    Solo `ok` (A con CAE legible) y `rechazado` (R) son definitivos. Todo lo que no se puede
    leer con certeza es `dudoso`: ARCA pudo autorizar y el llamador debe consultar antes de
    emitir otro número (FR-026).
    """
    campos = (
        "<ar:FeCAEReq><ar:FeCabReq><ar:CantReg>1</ar:CantReg>"
        f"<ar:PtoVta>{int(emisor.punto_venta)}</ar:PtoVta>"
        f"<ar:CbteTipo>{int(pedido['cbte_tipo'])}</ar:CbteTipo></ar:FeCabReq>"
        f"<ar:FeDetReq><ar:FECAEDetRequest>{_detalle(pedido)}</ar:FECAEDetRequest></ar:FeDetReq>"
        "</ar:FeCAEReq>"
    )
    resultado = _wsfe(emisor, ta, "FECAESolicitar", campos, escritura=True)
    if resultado.tipo != "ok":
        return resultado
    raiz = resultado.datos
    cabecera = _texto(raiz, ".//FECAESolicitarResult/FeCabResp/Resultado")
    detalle = _texto(raiz, ".//FECAEDetResponse/Resultado")
    cae = _texto(raiz, ".//FECAEDetResponse/CAE")
    try:
        vence = datetime.strptime(_texto(raiz, ".//FECAEDetResponse/CAEFchVto"), "%Y%m%d").date()
    except ValueError:
        vence = None
    if "R" in (cabecera, detalle):
        return Resultado("rechazado", error=_errores(raiz) or "ARCA rechazó el comprobante.")
    if cabecera == "A" and detalle == "A" and cae.isdigit() and vence:
        return Resultado("ok", {"cae": cae, "cae_vence": vence})
    if not cabecera and not cae and raiz.find(".//Errors/Err") is not None:
        return _con_errores(raiz)  # ARCA no llegó a procesar el pedido
    return Resultado("dudoso", error=_errores(raiz) or "Respuesta ambigua de ARCA.")


def consultar(emisor, ta: dict, cbte_tipo: int, numero: int) -> Resultado:
    """`FECompConsultar`. `rechazado` solo si ARCA dice que el comprobante no existe (602).

    Cualquier otra duda es `reintentable`: "no existe" habilita a emitir otro número.
    """
    campos = (
        f"<ar:FeCompConsReq><ar:CbteTipo>{int(cbte_tipo)}</ar:CbteTipo>"
        f"<ar:CbteNro>{int(numero)}</ar:CbteNro><ar:PtoVta>{int(emisor.punto_venta)}</ar:PtoVta>"
        "</ar:FeCompConsReq>"
    )
    resultado = _wsfe(emisor, ta, "FECompConsultar", campos)
    if resultado.tipo != "ok":
        return resultado
    raiz = resultado.datos
    cae = _texto(raiz, ".//ResultGet/CodAutorizacion")
    try:
        datos = {
            "cae": cae,
            "cae_vence": datetime.strptime(_texto(raiz, ".//ResultGet/FchVto"), "%Y%m%d").date(),
            "fecha": datetime.strptime(_texto(raiz, ".//ResultGet/CbteFch"), "%Y%m%d").date(),
            "doc_tipo": int(_texto(raiz, ".//ResultGet/DocTipo")),
            "doc_numero": _texto(raiz, ".//ResultGet/DocNro"),
            "total": Decimal(_texto(raiz, ".//ResultGet/ImpTotal")),
        }
    except (ValueError, ArithmeticError):
        datos = None
    if cae.isdigit() and datos:
        return Resultado("ok", datos)
    codigos = _codigos_error(raiz)
    if "602" in codigos and not codigos & {"600", "601"}:
        return Resultado("rechazado", error=_errores(raiz))
    return _con_errores(raiz, tokens={"600", "601"})
