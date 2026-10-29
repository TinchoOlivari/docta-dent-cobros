import json
from base64 import b64encode
from io import BytesIO

import pymupdf
import qrcode

from sistema.core.templatetags.pesos import pesos

from .models import ComprobanteFiscal

URL_QR = "https://www.afip.gob.ar/fe/qr/?p="
LETRA = {1: "A", 3: "A", 6: "B", 8: "B", 11: "C", 13: "C"}
MARCA_PRUEBA = "SIN VALIDEZ FISCAL — AMBIENTE DE PRUEBA"
DOCUMENTO = {80: "CUIT", 86: "CUIL", 96: "DNI", 99: "Consumidor final"}


def _emitido(comprobante):
    if comprobante.estado != ComprobanteFiscal.Estado.EMITIDO:
        raise ValueError("Solo un comprobante emitido tiene PDF y QR.")


def nombre_archivo(comprobante) -> str:
    base = "nota-credito" if comprobante.tipo == ComprobanteFiscal.Tipo.NOTA_CREDITO else "factura"
    return f"{base}-{comprobante.punto_venta:05d}-{comprobante.numero:08d}.pdf"


def url_qr(comprobante) -> str:
    """Research R2. El importe se escribe desde `Decimal`, nunca pasa por float."""
    _emitido(comprobante)
    datos = {
        "ver": 1,
        "fecha": comprobante.fecha.isoformat(),
        "cuit": int(comprobante.emisor.cuit),
        "ptoVta": comprobante.punto_venta,
        "tipoCmp": comprobante.cbte_tipo,
        "nroCmp": comprobante.numero,
        "importe": f"{comprobante.total:.2f}",
        "moneda": "PES",
        "ctz": 1,
        "tipoDocRec": comprobante.doc_tipo,
        "nroDocRec": int(comprobante.doc_numero or 0),
        "tipoCodAut": "E",
        "codAut": int(comprobante.cae),
    }
    cuerpo = ",".join(f'"{k}":{v if k == "importe" else json.dumps(v)}' for k, v in datos.items())
    return URL_QR + b64encode(("{" + cuerpo + "}").encode()).decode()


def _periodo(c):
    if c.servicio_desde and c.servicio_hasta:
        return [
            (10, f"Período del servicio: {c.servicio_desde:%d/%m/%Y} a {c.servicio_hasta:%d/%m/%Y}")
        ]
    return []


def _anula(c):
    f = c.asociado
    if c.tipo == ComprobanteFiscal.Tipo.NOTA_CREDITO and f and f.numero:
        return [(10, f"Anula la factura {f.punto_venta:05d}-{f.numero:08d}")]
    return []


def pdf_comprobante(comprobante) -> bytes:
    _emitido(comprobante)
    emisor, c = comprobante.emisor, comprobante
    titulo = f"{c.get_tipo_display()} {LETRA[c.cbte_tipo]}"
    lineas = [
        (16, emisor.razon_social),
        (10, f"CUIT {emisor.cuit}"),
        (10, f"Condición frente al IVA: {emisor.get_condicion_iva_display()}"),
        (None, None),
        (14, titulo),
        (11, f"N° {c.punto_venta:05d}-{c.numero:08d}"),
        (10, f"Fecha: {c.fecha:%d/%m/%Y}"),
        (None, None),
        (10, f"Receptor: {c.receptor_nombre or 'Consumidor final'}"),
        (10, f"{DOCUMENTO.get(c.doc_tipo, 'Documento')}: {c.doc_numero or '0'}"),
        (None, None),
        (10, f"Concepto: {c.get_concepto_display()}"),
        (10, c.descripcion),
        *_periodo(c),
        *_anula(c),
        (10, f"Neto: {pesos(c.neto)}"),
        (10, f"IVA: {pesos(c.iva)}"),
        (12, f"Total: {pesos(c.total)}"),
        (None, None),
        (10, f"CAE: {c.cae}"),
        (10, f"Vencimiento del CAE: {c.cae_vence:%d/%m/%Y}"),
    ]
    doc = pymupdf.open()
    pagina = doc.new_page(width=595, height=842)
    if emisor.ambiente == emisor.Ambiente.PRUEBA:
        pagina.insert_text((50, 36), MARCA_PRUEBA, fontsize=11, color=(0.75, 0, 0))
    y = 60
    for tamano, texto in lineas:
        if texto is not None:
            pagina.insert_text((50, y), texto, fontsize=tamano)
        y += 22
    png = BytesIO()
    qrcode.make(url_qr(c)).save(png, "PNG")
    pagina.insert_image(pymupdf.Rect(50, y + 10, 200, y + 160), stream=png.getvalue())
    return doc.tobytes()
