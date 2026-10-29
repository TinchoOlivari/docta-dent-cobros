"""Validación y compresión del comprobante (research R5)."""

from io import BytesIO

import fitz
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image

MAX_BYTES = 5 * 1024 * 1024
FORMATOS = ("JPEG", "PNG", "GIF")
MENSAJE_TIPO = "El comprobante debe ser una imagen (JPEG, PNG o GIF) o un PDF."
MENSAJE_DANADO = "No se pudo leer el archivo. Probá con otra foto o con un PDF."
OBJETIVO_KB = 50


def _leer_imagen(datos):
    try:
        with Image.open(BytesIO(datos)) as img:
            formato = img.format
            img.verify()
    except Exception:
        return None
    return Image.open(BytesIO(datos)) if formato in FORMATOS else None


def _leer_pdf(datos):
    try:
        doc = fitz.open(stream=datos, filetype="pdf")
        return doc if doc.page_count >= 1 else None
    except Exception:
        return None


def _jpeg(img):
    """Imagen a JPEG RGB de 1200 x 1600 como máximo; calidad 70 bajando de a 10 (copia de prod)."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        fondo = Image.new("RGB", img.size, (255, 255, 255))
        fondo.paste(img, mask=img.split()[-1])
        img = fondo
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1200, 1600), Image.Resampling.LANCZOS)
    calidad = 70
    while True:
        salida = BytesIO()
        img.save(salida, format="JPEG", quality=calidad, optimize=True)
        if salida.tell() / 1024 <= OBJETIVO_KB or calidad <= 30:
            return salida.getvalue()
        calidad -= 10


def validar_y_comprimir(archivo):
    if archivo.size > MAX_BYTES:
        raise ValidationError("El comprobante pesa más de 5 MB.")
    archivo.seek(0)
    datos = archivo.read()
    img = _leer_imagen(datos)
    if img is not None:
        try:
            return ContentFile(_jpeg(img), name="comprobante.jpg")
        except Exception as e:  # Pillow: datos cortados que verify() no detecta
            raise ValidationError(MENSAJE_DANADO) from e
    doc = _leer_pdf(datos)
    if doc is None:
        raise ValidationError(MENSAJE_TIPO)
    # ponytail: PDF de varias páginas guarda solo la primera, igual que prod
    zoom = 100 / 72
    try:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        salida = BytesIO()
        pix.pil_save(salida, format="JPEG", optimize=True, quality=75)
    except Exception as e:  # pymupdf (FileDataError, RuntimeError, FzErrorFormat) o Pillow
        raise ValidationError(MENSAJE_DANADO) from e
    return ContentFile(salida.getvalue(), name="comprobante.jpg")
