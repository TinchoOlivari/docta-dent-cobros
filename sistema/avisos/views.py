import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from sistema.core.permisos import solo_administrador

from .forms import DireccionForm, MargenForm
from .models import DireccionAviso
from .servicios import agregar_direccion, borrar_direccion, guardar_margen, margen

logger = logging.getLogger(__name__)


def _pantalla(request, form, direccion_form):
    direcciones = DireccionAviso.objects.all()
    return render(
        request,
        "avisos/configuracion.html",
        {
            "form": form,
            "direccion_form": direccion_form,
            "direcciones": direcciones,
            "sin_direcciones": not direcciones,
        },
    )


@solo_administrador
def configuracion(request):
    if request.method == "POST":
        form = MargenForm(request.POST)
        if form.is_valid():
            guardar_margen(usuario=request.user, margen=form.cleaned_data["margen"])
            messages.success(request, "Margen guardado.")
            return redirect("avisos:configuracion")
    else:
        form = MargenForm(initial={"margen": margen()})
    return _pantalla(request, form, DireccionForm())


@solo_administrador
@require_POST
def agregar(request):
    form = DireccionForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError("Escribí una dirección de email válida.")
        agregar_direccion(usuario=request.user, email=form.cleaned_data["email"])
    except ValidationError as e:
        logger.warning("Dirección de aviso rechazada: user=%s", request.user.username)
        # El rechazo queda en el campo, con lo que escribió (021/FR-045, FR-054).
        for mensaje in e.messages:
            form.add_error("email", mensaje)
        return _pantalla(request, MargenForm(initial={"margen": margen()}), form)
    messages.success(request, "Dirección agregada.")
    return redirect("avisos:configuracion")


@solo_administrador
@require_POST
def borrar(request, pk):
    borrar_direccion(get_object_or_404(DireccionAviso, pk=pk), usuario=request.user)
    messages.success(request, "Dirección borrada.")
    return redirect("avisos:configuracion")
