from decimal import Decimal

from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.cobros.models import ItemCobro
from sistema.dentalink.models import Banco, MedioPago
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

_contador = 0


def cuenta_transferencia(banco="Banco Inventado"):
    global _contador
    _contador += 1
    destinatario = crear_destinatario(usuario=None, nombre=f"Dest {_contador}", tipo="profesional")
    return crear_cuenta(
        usuario=None,
        tipo="transferencia",
        destinatario=destinatario,
        alias=f"alias.prueba.{_contador}",
        banco=banco,
        tope_mensual=Decimal("500000.00"),
    )


def cobro_con_items(
    montos, *, metodo="transferencia", estado="finalizado", monto=None, cuenta=None
):
    montos = [Decimal(m) for m in montos]
    cobro = cobro_prueba(
        cuenta=cuenta or cuenta_transferencia(),
        monto=Decimal(monto) if monto is not None else sum(montos, Decimal("0.00")),
        estado=estado,
        metodo=metodo,
        id_paciente_dentalink=501,
    )
    for n, m in enumerate(montos, start=1):
        ItemCobro.objects.create(cobro=cobro, numero=n, id_prestacion=9000 + n, monto=m)
    return cobro


def copia_basica():
    nombres = ["Efectivo", "Transferencia", "Tarjeta de débito", "Tarjeta de crédito", "Contado"]
    for i, nombre in enumerate(nombres, start=1):
        MedioPago.objects.create(id_dentalink=i, nombre=nombre)
    Banco.objects.create(id_dentalink=20, nombre="Banco Inventado")
    return Banco.objects.create(id_dentalink=99, nombre="Otro")
