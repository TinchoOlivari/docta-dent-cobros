"""Único lugar que habla HTTP con Dentalink (research R1 a R3)."""

import http.client
import json
import logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_LECTURA = 3  # GET, SC-003
TIMEOUT_CONECTAR = 5  # POST
TIMEOUT_LEER = 15  # POST
# ponytail: tope fijo de pedidos simultáneos; bajarlo si Dentalink empieza a responder 429.
PEDIDOS_EN_PARALELO = 8


class DentalinkNoDisponible(Exception):
    def __init__(
        self,
        mensaje="Dentalink no está disponible. Podés crear el cobro sin vincularlo con Dentalink.",
    ):
        super().__init__(mensaje)


@dataclass(frozen=True)
class Resultado:
    tipo: str  # ok, sin_id, reintentable, rechazado, dudoso
    id_pago: int | None = None
    error: str = ""


def _base():
    partes = urllib.parse.urlsplit(settings.DENTALINK_URL)
    return partes.netloc, partes.path.rstrip("/")


def _conexion(timeout):
    host, _ = _base()
    return http.client.HTTPSConnection(host, timeout=timeout)


def _headers():
    return {
        "Authorization": f"Token {settings.DENTALINK_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _get(ruta, params=None):
    if not settings.DENTALINK_TOKEN:
        raise DentalinkNoDisponible()
    url = f"{_base()[1]}{ruta}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    conn = None
    try:
        conn = _conexion(TIMEOUT_LECTURA)
        conn.request("GET", url, headers=_headers())
        respuesta = conn.getresponse()
        status = respuesta.status
        cuerpo = respuesta.read()
    except (OSError, http.client.HTTPException):
        logger.warning("Dentalink no responde: ruta=%s", ruta)
        raise DentalinkNoDisponible() from None
    finally:
        if conn is not None:
            conn.close()
    if not 200 <= status < 300:
        logger.warning("Dentalink respondió con error: ruta=%s status=%s", ruta, status)
        raise DentalinkNoDisponible()
    try:
        return json.loads(cuerpo, parse_float=Decimal)["data"]
    except (ValueError, KeyError, TypeError):
        logger.warning("Dentalink devolvió JSON inválido: ruta=%s status=%s", ruta, status)
        raise DentalinkNoDisponible() from None


def _decimal(valor):
    return Decimal(str(valor if valor is not None else 0))


def _nacimiento(valor):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


def _datos_paciente(p):
    # apellidos y fecha_nacimiento: confirmar con la API real (005/T062)
    nombre = " ".join(x for x in (p.get("nombre"), p.get("apellidos")) if x)
    return {
        "id": p["id"],
        "nombre": nombre,
        "dni": p.get("rut") or "",
        "nacimiento": _nacimiento(p.get("fecha_nacimiento")),
    }


def _buscar(q):
    return _get("/pacientes", {"q": urllib.parse.quote(json.dumps(q))})


# Endpoints y campos a confirmar con la API real: tasks.md T060
def buscar_pacientes(texto):
    if texto and all(c.isdigit() or c == "." for c in texto):
        datos = _buscar({"rut": {"eq": texto.replace(".", "")}})
    else:
        # Nombre O apellidos: 2 pedidos en serie (peor caso 2 x TIMEOUT_LECTURA). El filtro exacto
        # se confirma en 005/T062; con un filtro O en un solo pedido, 005/T060 lo cambia.
        vistos = {}
        for campo in ("nombre", "apellidos"):
            for p in _buscar({campo: {"lk": texto}}):
                vistos.setdefault(p["id"], p)
        datos = list(vistos.values())
    return [_datos_paciente(p) for p in datos]


def paciente(id_paciente):
    return _datos_paciente(_get(f"/pacientes/{id_paciente}"))


def tratamientos(id_paciente):
    resultado = []
    lista = _get(f"/pacientes/{id_paciente}/tratamientos")
    # Un pedido por tratamiento, en paralelo: en serie eran ~0,6 s cada uno. `map` conserva el orden
    # y relanza el primer DentalinkNoDisponible.
    with ThreadPoolExecutor(max_workers=PEDIDOS_EN_PARALELO) as hilos:
        todos = list(hilos.map(lambda t: _get(f"/tratamientos/{t['id']}/detalles"), lista))
    for t, detalles in zip(lista, todos, strict=True):
        # La API real no trae "profesionales": salen del tratamiento y de cada detalle (T060).
        nombres = {p["id"]: p["nombre"] for p in t.get("profesionales", [])}
        if t.get("id_dentista") and t.get("nombre_dentista"):
            nombres.setdefault(t["id_dentista"], t["nombre_dentista"])
        for d in detalles:
            if d.get("id_dentista_realizador") and d.get("dentista_realizador"):
                nombres.setdefault(d["id_dentista_realizador"], d["dentista_realizador"])
        resultado.append(
            {
                "id": t["id"],
                "nombre": t.get("nombre", ""),
                "id_profesional": t.get("id_dentista"),
                "profesionales": [{"id": k, "nombre": v} for k, v in nombres.items()],
                "prestaciones": [
                    {
                        "id": d["id"],
                        "id_prestacion": d.get("id_prestacion"),
                        "nombre": d.get("nombre_prestacion") or d.get("nombre", ""),
                        "id_profesional": d.get("id_dentista_realizador") or d.get("id_dentista"),
                        "total": _decimal(d.get("total")),
                        "pagado": _decimal(d.get("pagado")),
                    }
                    for d in detalles
                ],
            }
        )
    return resultado


RUTAS = {"mediospago": "medios"}  # API real (T060)


def listar(recurso):
    return [{"id": r["id"], "nombre": r["nombre"]} for r in _get(f"/{RUTAS.get(recurso, recurso)}")]


def _texto_error(texto):
    """Texto legible de un rechazo: `error`, `message` o `mensaje` del JSON; si no, el cuerpo (200)."""
    try:
        cuerpo = json.loads(texto)
    except ValueError:
        cuerpo = None
    if isinstance(cuerpo, dict):
        for clave in ("error", "message", "mensaje"):
            if isinstance(cuerpo.get(clave), str) and cuerpo[clave].strip():
                return cuerpo[clave].strip()
    return texto[:200]


def crear_pago(datos):
    """POST /pagos. Nunca lanza (research R3)."""
    if not settings.DENTALINK_TOKEN:
        return Resultado("reintentable", error="Falta DENTALINK_TOKEN.")
    conn = _conexion(TIMEOUT_CONECTAR)
    try:
        try:
            conn.connect()
        except OSError as exc:
            return Resultado("reintentable", error=f"No se pudo conectar: {exc}")
        try:
            # Desde acá el pago pudo crearse: cualquier corte es dudoso.
            conn.sock.settimeout(TIMEOUT_LEER)
            conn.request("POST", f"{_base()[1]}/pagos", body=json.dumps(datos), headers=_headers())
            respuesta = conn.getresponse()
            status = respuesta.status
            cuerpo = respuesta.read()
        except (OSError, http.client.HTTPException) as exc:
            return Resultado("dudoso", error=f"Sin respuesta completa: {exc}")
    finally:
        conn.close()
    texto = cuerpo.decode("utf-8", errors="replace") if isinstance(cuerpo, bytes) else str(cuerpo)
    if status >= 500:
        return Resultado("reintentable", error=f"Dentalink respondió {status}.")
    if 400 <= status < 500:
        return Resultado("rechazado", error="Dentalink rechazó el pago: " + _texto_error(texto))
    if not 200 <= status < 300:
        return Resultado("dudoso", error=f"Respuesta inesperada {status}.")
    try:
        id_pago = json.loads(texto)["data"]["id"]
    except (ValueError, KeyError, TypeError):
        id_pago = None
    if isinstance(id_pago, int) and not isinstance(id_pago, bool):
        return Resultado("ok", id_pago=id_pago)
    return Resultado("sin_id", error="Dentalink no devolvió el id del pago.")
