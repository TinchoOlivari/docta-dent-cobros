import re
from datetime import timedelta
from decimal import Decimal

import django_filters as df
from django import forms
from django.db.models import Q

from sistema.arca.models import ComprobanteFiscal
from sistema.arca.servicios import FACTURABLE_Q
from sistema.cobros.models import Cobro, ItemCobro
from sistema.core.tiempo import inicio_dia_arg
from sistema.dentalink.models import Profesional
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.terminales.models import Terminal
from sistema.usuarios import roles
from sistema.usuarios.servicios import usuarios_visibles


def _combo():
    # Combobox con búsqueda (Tom Select lo toma por `data-combo`, 018/FR-019). Un widget por campo.
    return forms.Select(attrs={"data-combo": ""})


_MONTO = re.compile(r"\d{1,12}(\.\d{1,2})?")


def _monto(q):
    """`70000`, `70000,50` o `70.000,50` a Decimal. Si no se lee como monto, None."""
    texto = q.replace(".", "").replace(",", ".")
    return Decimal(texto) if _MONTO.fullmatch(texto) else None


class Casilla(df.Filter):
    """Casilla: `on` es verdadero; sin marcar no filtra."""

    field_class = forms.BooleanField

    def __init__(self, **kwargs):
        kwargs.setdefault("widget", forms.CheckboxInput)
        super().__init__(**kwargs)


class FiltroCobros(df.FilterSet):
    """Los 16 parámetros de la lista de cobros del administrador (016/FR-019).
    Un valor inválido no filtra; el resto sí."""

    desde = df.DateFilter(
        method="filtrar_desde", widget=forms.DateInput(attrs={"type": "date"}), label="Desde"
    )
    hasta = df.DateFilter(
        method="filtrar_hasta", widget=forms.DateInput(attrs={"type": "date"}), label="Hasta"
    )
    estado = df.ChoiceFilter(
        choices=Cobro.Estado.choices,
        empty_label="Todos",
        label="Estado",
    )
    metodo = df.ChoiceFilter(
        choices=Cobro.Metodo.choices, empty_label="Todos los métodos", label="Método"
    )
    destinatario = df.ModelChoiceFilter(
        queryset=Destinatario.objects.visibles().order_by("nombre"),
        empty_label="Todos",
        label="Destinatario",
        widget=_combo(),
    )
    cuenta = df.ModelChoiceFilter(
        queryset=Cuenta.objects.para_selector(), empty_label="Todas las cuentas", label="Cuenta"
    )
    terminal = df.ModelChoiceFilter(
        queryset=Terminal.objects.all(), empty_label="Todas", label="Terminal POS"
    )
    creador = df.ModelChoiceFilter(
        queryset=usuarios_visibles().filter(role__in=[roles.ADMIN, roles.OPERATOR]),
        field_name="creado_por",
        empty_label="Todos",
        label="Creado por",
        widget=_combo(),
    )
    profesional = df.ModelChoiceFilter(
        queryset=Profesional.objects.all(),
        method="filtrar_items",
        empty_label="Todos",
        label="Profesional",
        widget=_combo(),
    )
    estado_envio = df.ChoiceFilter(
        choices=ItemCobro.EstadoEnvio.choices,
        method="filtrar_items",
        empty_label="Todos",
        label="Estado de envío",
    )
    excedente_sin_revisar = Casilla(method="filtrar_excedente", label="Excedente sin revisar")
    faltan_items = Casilla(method="filtrar_faltan", label="Sin vincular a Dentalink")
    revisado = df.ChoiceFilter(
        choices=[("si", "Revisados"), ("no", "No revisados")],
        method="filtrar_revisado",
        empty_label="Todos",
        label="Revisado",
    )
    factura_con_error = Casilla(method="filtrar_con_error", label="Factura con error")
    factura_a_revisar = Casilla(method="filtrar_a_revisar", label="Factura a revisar")
    q = df.CharFilter(
        method="buscar", label="Número, paciente, alias, destinatario, observaciones o monto"
    )

    class Meta:
        model = Cobro
        fields = ["estado", "metodo", "destinatario", "cuenta", "terminal"]

    def __init__(self, data=None, *args, **kwargs):
        if data is not None and data.get("revisado") in ("on", "1"):
            data = data.copy()  # `on` y `1` de los enlaces viejos valen `si`
            data["revisado"] = "si"
        super().__init__(data, *args, **kwargs)

    def filtrar_desde(self, qs, name, value):
        return qs.filter(fecha__gte=inicio_dia_arg(value))

    def filtrar_hasta(self, qs, name, value):
        return qs.filter(fecha__lt=inicio_dia_arg(value + timedelta(days=1)))

    def filtrar_items(self, qs, name, value):
        # `profesional` y `estado_envio` miran el mismo ítem: se aplican juntos, una sola vez.
        if name == "estado_envio" and self.form.cleaned_data.get("profesional"):
            return qs  # ya lo aplicó `profesional`
        c = self.form.cleaned_data
        items = ItemCobro.objects.all()
        if c.get("profesional"):
            items = items.filter(id_profesional=c["profesional"].id_dentalink)
        if c.get("estado_envio"):
            items = items.filter(estado_envio=c["estado_envio"])
        return qs.filter(pk__in=items.values("cobro"))

    def filtrar_excedente(self, qs, name, value):
        return qs.filter(excedente=True, revisado=False) if value else qs

    def filtrar_faltan(self, qs, name, value):
        return qs.filter(faltan_items=True) if value else qs

    def filtrar_revisado(self, qs, name, value):
        return qs.filter(revisado=value == "si")

    @staticmethod
    def _q_factura_error():
        # 021/FR-039: la factura en reintento cuenta desde el primer fallo.
        con_error = ComprobanteFiscal.objects.filter(
            Q(estado="error") | Q(estado="pendiente", intentos__gte=1)
        ).values("cobro")
        vigentes = ComprobanteFiscal.objects.filter(tipo="factura", vigente=True).values("cobro")
        # 020/FR-018: también el Finalizado facturable sin factura vigente
        return Q(pk__in=con_error) | (FACTURABLE_Q & ~Q(pk__in=vigentes))

    def filtrar_con_error(self, qs, name, value):
        return qs.filter(self._q_factura_error()) if value else qs

    def filtrar_a_revisar(self, qs, name, value):
        if not value:
            return qs
        return qs.filter(
            pk__in=ComprobanteFiscal.objects.filter(
                tipo="factura", vigente=True, a_revisar=True
            ).values("cobro")
        )

    def buscar(self, qs, name, value):
        q = value.strip()
        if not q:
            return qs
        cond = (
            Q(nombre_paciente__unaccent__icontains=q)
            | Q(cuenta__alias__unaccent__icontains=q)
            | Q(observaciones__unaccent__icontains=q)
            | Q(destinatario__nombre__unaccent__icontains=q)
        )
        if q.isdigit() and len(q) <= 18:
            cond |= Q(pk=int(q))
        if (monto := _monto(q)) is not None:
            cond |= Q(monto=monto)
        return qs.filter(cond)


class FiltroCobrosOperador(df.FilterSet):
    """Búsqueda del operador sobre sus cobros (017/FR-011, FR-012). Valor inválido: no filtra."""

    q = df.CharFilter(method="buscar", label="Buscar cobros")
    estado = df.ChoiceFilter(
        choices=[("pendiente", "Pendientes")],
        empty_label="Todos",
        label="Estado",
        widget=forms.RadioSelect,
    )

    class Meta:
        model = Cobro
        fields = ["estado"]

    def buscar(self, qs, name, value):
        q = value.strip()
        if not q:
            return qs
        cond = Q(nombre_paciente__unaccent__icontains=q)
        if q.isdigit() and len(q) <= 18:
            cond |= Q(pk=int(q))
        return qs.filter(cond)
