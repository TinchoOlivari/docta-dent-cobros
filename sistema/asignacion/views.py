from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from sistema.core.permisos import solo_administrador
from sistema.destinatarios.models import Cuenta

from .servicios import lista_con_cupo, mover_cuenta


@solo_administrador
def lista(request):
    return render(request, "asignacion/lista.html", {"cuentas": lista_con_cupo()})


@solo_administrador
@require_POST
def mover(request, pk):
    cuenta = get_object_or_404(Cuenta, pk=pk)
    try:
        posicion = int(request.POST["posicion"])
    except (KeyError, ValueError):
        messages.error(request, "Escribí un número de posición.")
        return redirect("asignacion:lista")
    try:
        mover_cuenta(cuenta, posicion, usuario=request.user)
    except ValidationError as e:
        for mensaje in e.messages:
            messages.error(request, mensaje)
    else:
        messages.success(request, "Lista de prioridad actualizada.")
    return redirect("asignacion:lista")
