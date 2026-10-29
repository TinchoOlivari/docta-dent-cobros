import re
from itertools import count
from datetime import date, datetime

from sistema.cobros.models import METODOS_POS, METODOS_TARJETA, Cobro, PagoComplementario
from sistema.dentalink.models import Banco
from sistema.destinatarios.models import Proveedor
from sistema.terminales.models import Terminal

_operaciones = count(1)


def cobro_prueba(**campos) -> Cobro:
    """Crea un Cobro completando lo que las constraints de 007 exigen según método y estado."""
    metodo = campos.get("metodo", "transferencia")
    estado = campos.get("estado", "pendiente")
    pos = metodo in METODOS_POS
    if pos or (metodo == "transferencia" and estado == "finalizado"):
        campos.setdefault("comprobante", "comprobantes/prueba.pdf")
    if pos:
        if "monto_neto" not in campos:
            campos["monto_neto"] = campos["monto"]
        if "terminal" not in campos and "terminal_id" not in campos:
            campos["terminal"] = Terminal.objects.get_or_create(
                nombre="Terminal Prueba",
                defaults={
                    "proveedor": Proveedor.objects.get(nombre="Mercadopago"),
                    "acepta_debito": True,
                    "acepta_credito": True,
                    "acepta_qr": True,
                    "acepta_link": True,
                },
            )[0]
        campos.setdefault("numero_operacion", f"OP{next(_operaciones)}")
    if metodo in METODOS_TARJETA:
        if "banco_emisor" not in campos and "banco_emisor_id" not in campos:
            campos["banco_emisor"] = Banco.objects.get_or_create(
                id_dentalink=777, defaults={"nombre": "Banco Prueba"}
            )[0]
        campos.setdefault("ultimos_4", "0000")
    if estado == "cancelado":
        campos.setdefault("motivo_cancelacion", "otro_medio")
    return Cobro.objects.create(**campos)


def con_elegidas(datos):
    """023/FR-037: marca (`elegida_<id>=on`) cada prestación con `monto_<id>` escrito."""
    marcadas = {
        "elegida_" + k[len("monto_") :]: "on"
        for k, v in datos.items()
        if re.fullmatch(r"monto_\d+", k) and v
    }
    return {**datos, **marcadas}


def post_nuevo(montos, **resto):
    """Datos de `cobros:nuevo` o `cobros:items`: `montos` = {id de prestación: texto del monto}."""
    return con_elegidas({**{f"monto_{k}": v for k, v in montos.items()}, **resto})


def pago_prueba(**campos) -> PagoComplementario:
    """Crea un PagoComplementario sin pasar por clean()."""
    campos.setdefault("metodo", "transferencia")
    campos.setdefault("mes", date(2026, 10, 1))
    if campos.get("estado") == "anulado":
        campos.setdefault("motivo_anulacion", "datos_erroneos")
    return PagoComplementario.objects.create(**campos)


def topes_desde_enero():
    """021/FR-027: un mes anterior al primer tope espera $0,00. Las cuentas de prueba nacen con el
    reloj de la prueba; esto las hace regir desde enero de 2026."""
    from sistema.core.tiempo import ARG
    from sistema.destinatarios.models import TopeVigente

    TopeVigente.objects.update(desde=datetime(2026, 1, 1, tzinfo=ARG))
