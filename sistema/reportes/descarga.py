import csv
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from sistema.core.tiempo import ARG

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Texto cargado por un operador (paciente, observaciones) no debe correr como fórmula.
FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _csv(valor):
    if valor is None:
        return ""
    if isinstance(valor, Decimal):
        return f"{valor:.2f}".replace(".", ",")
    if isinstance(valor, datetime):
        return timezone.localtime(valor, ARG).strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, str) and valor.startswith(FORMULA):
        return "'" + valor
    return valor


def _celda(hoja, valor):
    if isinstance(valor, Decimal):
        celda = WriteOnlyCell(hoja, value=valor)
        celda.number_format = "#,##0.00"
        return celda
    if isinstance(valor, datetime):
        celda = WriteOnlyCell(hoja, value=timezone.localtime(valor, ARG).replace(tzinfo=None))
        celda.number_format = "dd/mm/yyyy hh:mm"
        return celda
    if isinstance(valor, str) and valor.startswith("="):
        celda = WriteOnlyCell(hoja, value=valor)
        celda.data_type = "s"
        return celda
    return valor


class _Eco:
    """Patrón «Echo» de Django: `csv.writer` escribe y devuelve la línea, sin juntar todo en memoria."""

    def write(self, linea):
        return linea


def _lineas_csv(titulos, filas):
    yield "\ufeff"
    escritor = csv.writer(_Eco(), delimiter=";")
    yield escritor.writerow(titulos)
    for fila in filas:
        yield escritor.writerow([_csv(v) for v in fila])


def respuesta(tipo, titulos, filas, formato, ahora=None, nombre=None):
    """Descarga `filas` como CSV (`;`, coma decimal, BOM; en streaming) o XLSX (números y fechas
    reales). Nombre del archivo: `nombre` o `<tipo>-<dd-mm-aaaa>` (021/FR-061)."""
    if formato not in ("csv", "xlsx"):
        raise ValueError(formato)
    fecha = timezone.localtime(ahora or timezone.now(), ARG).strftime("%d-%m-%Y")
    if formato == "csv":
        r = StreamingHttpResponse(
            _lineas_csv(titulos, filas), content_type="text/csv; charset=utf-8"
        )
    else:
        libro = Workbook(write_only=True)
        hoja = libro.create_sheet()
        hoja.append(titulos)
        for fila in filas:
            hoja.append([_celda(hoja, v) for v in fila])
        salida = BytesIO()
        libro.save(salida)
        r = HttpResponse(salida.getvalue(), content_type=XLSX)
    r["Content-Disposition"] = f'attachment; filename="{nombre or f"{tipo}-{fecha}"}.{formato}"'
    return r
