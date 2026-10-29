from decimal import Decimal

from django import forms
from django.db.models import Q

from sistema.core.forms import FechaHoraField, MesField, MontoDecimalField
from sistema.cobros.models import Cobro, PagoComplementario
from sistema.dentalink.models import Banco
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.terminales.models import Terminal

# Los mensajes de error vienen de MontoDecimalField (020/FR-030).
MONTO = {"max_digits": 12, "decimal_places": 2, "localize": True}

MENSAJE_OBSERVACIONES = "Las observaciones pueden tener hasta 1.000 caracteres."
MAX_OBSERVACIONES = 1000


class ObservacionesForm(forms.Form):
    """020/FR-007: observaciones del detalle y de «adjuntar comprobante»."""

    observaciones = forms.CharField(
        required=False,
    )

    def clean_observaciones(self):
        # El navegador cuenta un salto de línea como 1 carácter; HTTP lo manda como \r\n.
        texto = self.cleaned_data["observaciones"].replace("\r\n", "\n")
        if len(texto) > MAX_OBSERVACIONES:
            raise forms.ValidationError(MENSAJE_OBSERVACIONES)
        return texto


class _MetodoForm(forms.Form):
    """Campos de método de cobro y datos POS, comunes al alta normal y a «faltan ítems»."""

    metodo = forms.ChoiceField(
        choices=Cobro.Metodo.choices, label="Método de cobro", widget=forms.RadioSelect
    )
    terminal = forms.ModelChoiceField(Terminal.objects.none(), required=False)
    monto_neto = MontoDecimalField(required=False, label="Neto acreditado", **MONTO)
    numero_operacion = forms.CharField(required=False, max_length=30, label="Número de operación")
    banco_emisor = forms.ModelChoiceField(
        Banco.objects.filter(vigente=True), required=False, label="Banco emisor"
    )
    ultimos_4 = forms.CharField(
        required=False,
        max_length=4,
        label="Últimos 4 dígitos",
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "pattern": "[0-9]{4}",
                "maxlength": "4",
                "autocomplete": "off",
                "data-mensaje": "Escribí los 4 dígitos.",
            }
        ),
    )
    comprobante = forms.FileField(required=False, label="Comprobante")

    def __init__(self, *args, metodos_pos_terminales, **kwargs):
        """`metodos_pos_terminales`: {método POS: terminales disponibles para ese método}."""
        super().__init__(*args, **kwargs)
        por_terminal = {}
        for metodo, terminales in metodos_pos_terminales.items():
            for t in terminales:
                por_terminal.setdefault(t.pk, (t, []))[1].append(metodo)
        self.terminales = [(t, " ".join(m)) for t, m in por_terminal.values()]
        self.fields["terminal"].queryset = Terminal.objects.filter(pk__in=por_terminal)

    def datos_metodo(self):
        d = self.cleaned_data
        return {
            "metodo": d["metodo"],
            "terminal": d["terminal"],
            "monto_neto": d["monto_neto"],
            "numero_operacion": d["numero_operacion"] or None,
            "banco_emisor": d["banco_emisor"],
            "ultimos_4": d["ultimos_4"] or None,
            "comprobante": d["comprobante"],
        }


class CobroForm(_MetodoForm):
    def __init__(self, *args, prestaciones, metodos_pos_terminales, **kwargs):
        super().__init__(*args, metodos_pos_terminales=metodos_pos_terminales, **kwargs)
        self.filas = []
        for t in prestaciones:
            for p in t["prestaciones"]:
                # 023/FR-037: solo una prestación marcada es ítem.
                self.fields[f"elegida_{p['id']}"] = forms.BooleanField(
                    required=False,
                    label=f"Elegir {p['nombre']}",
                    widget=forms.CheckboxInput(attrs={"aria-label": f"Elegir {p['nombre']}"}),
                )
                self.fields[f"monto_{p['id']}"] = MontoDecimalField(
                    required=False, label="Monto", **MONTO
                )
                if not p["saldo_a_favor"]:  # 021/FR-002: el navegador valida contra este tope
                    centavos = int(p["saldo_disponible"] * 100)
                    self.fields[f"monto_{p['id']}"].widget.attrs["data-saldo"] = centavos
                fila = {
                    "tratamiento": t,
                    "prestacion": p,
                    "profesional": None,
                    # 024/FR-002: lo que otros cobros pendientes ya reservan de esta prestación
                    "reservado": max(p["saldo"] - p["saldo_disponible"], Decimal("0.00")),
                }
                if not p["saldo_a_favor"]:
                    self.fields[f"profesional_{p['id']}"] = forms.ChoiceField(
                        choices=[(x["id"], x["nombre"]) for x in p["profesionales"]],
                        initial=p["profesional_propuesto"]["id"],
                        required=False,
                        label="Profesional",
                        # 024/FR-006: la fila marca un profesional distinto del propuesto
                        widget=forms.Select(
                            attrs={"data-propuesto": p["profesional_propuesto"]["id"]}
                        ),
                    )
                self.filas.append(fila)
        for fila in self.filas:  # los BoundField existen recién con todos los campos cargados
            id_ = fila["prestacion"]["id"]
            fila["elegida"] = self[f"elegida_{id_}"]
            fila["monto"] = self[f"monto_{id_}"]
            if not fila["prestacion"]["saldo_a_favor"]:
                fila["profesional"] = self[f"profesional_{id_}"]
        # 023/FR-036 y 024/FR-003: una rama por tratamiento con sus subtotales; no cuentan la
        # prestación de saldo a favor.
        self.ramas = []
        for t in prestaciones:
            filas = [f for f in self.filas if f["tratamiento"] is t]
            reales = [f["prestacion"] for f in filas if not f["prestacion"]["saldo_a_favor"]]

            def suma(clave, reales=reales):
                return sum((p.get(clave) or Decimal("0.00") for p in reales), Decimal("0.00"))

            self.ramas.append(
                {
                    "tratamiento": t,
                    "precio": suma("total"),
                    "abonado": suma("pagado"),
                    "saldo": suma("saldo_disponible"),
                    "filas": filas,
                }
            )

    def clean(self):
        datos = super().clean()
        marcadas = 0
        for fila in self.filas:
            id_ = fila["prestacion"]["id"]
            if not datos.get(f"elegida_{id_}"):
                continue
            marcadas += 1
            # Un monto inválido ya tiene su error; solo falta avisar del vacío.
            if datos.get(f"monto_{id_}") is None and f"monto_{id_}" not in self.errors:
                self.add_error(f"monto_{id_}", "Escribí el monto de esta prestación.")
        if not marcadas:  # 023/FR-037: reemplaza el error de 021/FR-002
            self.add_error(None, "Elegí al menos una prestación.")
        return datos

    def items(self):
        """Ítems del contrato: solo las prestaciones marcadas (023/FR-037)."""
        d = self.cleaned_data
        resultado = []
        for fila in self.filas:
            p = fila["prestacion"]
            if not d.get(f"elegida_{p['id']}"):
                continue
            monto = d.get(f"monto_{p['id']}")
            if monto is None:
                continue
            profesional = d.get(f"profesional_{p['id']}")
            resultado.append(
                {
                    "id_prestacion": p["id"],
                    "monto": monto,
                    "id_profesional": int(profesional) if profesional else None,
                    "saldo_a_favor": p["saldo_a_favor"],
                }
            )
        return resultado


class FaltanItemsForm(_MetodoForm):
    nombre_paciente = forms.CharField(max_length=200, label="Nombre del paciente")
    monto = MontoDecimalField(label="Monto", **MONTO)


class EditarCobroForm(forms.Form):
    """Correcciones del administrador (T072). `prestaciones` vacío = Dentalink caído."""

    fecha = FechaHoraField(label="Fecha y hora")
    monto = MontoDecimalField(**MONTO)
    metodo = forms.ChoiceField(choices=Cobro.Metodo.choices, label="Método de cobro")
    cuenta = forms.ModelChoiceField(
        Cuenta.objects.filter(tipo="transferencia").para_selector(),
        required=False,
        label="Cuenta",
    )
    terminal = forms.ModelChoiceField(Terminal.objects.all(), required=False)
    monto_neto = MontoDecimalField(required=False, label="Neto acreditado", **MONTO)
    numero_operacion = forms.CharField(required=False, max_length=30, label="Número de operación")
    banco_emisor = forms.ModelChoiceField(
        Banco.objects.filter(vigente=True), required=False, label="Banco emisor"
    )
    ultimos_4 = forms.CharField(
        required=False,
        max_length=4,
        label="Últimos 4 dígitos",
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "pattern": "[0-9]{4}",
                "maxlength": "4",
                "autocomplete": "off",
            }
        ),
    )
    comprobante = forms.FileField(required=False, label="Comprobante")
    observaciones = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    estado_visto = forms.CharField(widget=forms.HiddenInput)
    nuevo_prestacion = forms.ChoiceField(required=False, label="Prestación")
    nuevo_monto = MontoDecimalField(required=False, label="Monto", **MONTO)
    nuevo_profesional = forms.ChoiceField(required=False, label="Profesional")

    def __init__(self, *args, cobro, prestaciones=(), **kwargs):
        self.cobro = cobro
        kwargs.setdefault("initial", self.valores(cobro))
        super().__init__(*args, **kwargs)
        self.fields["cuenta"].label_from_instance = lambda c: c.etiqueta_selector
        fecha, hora = self.fields["fecha"].widget.widgets  # el MultiWidget no pasa por el filtro
        fecha.attrs.update({"class": "input", "aria-label": "Fecha del cobro"})
        hora.attrs.update({"class": "input campo-corto", "aria-label": "Hora del cobro"})
        if cobro.estado == Cobro.Estado.PENDIENTE:  # FR-005: el método no cambia en un Pendiente
            del self.fields["metodo"]
        todas = [(p, t) for t in prestaciones for p in t["prestaciones"]]
        profesionales = {}
        for p, _ in todas:
            for x in p["profesionales"]:
                profesionales[x["id"]] = x["nombre"]
        self.hay_dentalink = bool(todas)
        self.fields["nuevo_prestacion"].choices = [("", "Elegí…")] + [
            (p["id"], p["nombre"]) for p, _ in todas if not p["saldo_a_favor"]
        ]
        self.fields["nuevo_profesional"].choices = [("", "Propuesto")] + list(profesionales.items())
        self.filas = []
        for item in cobro.items.order_by("numero"):
            opciones = dict(profesionales)
            if item.id_profesional:
                opciones.setdefault(item.id_profesional, item.nombre_profesional)
            self.fields[f"monto_item_{item.pk}"] = MontoDecimalField(initial=item.monto, **MONTO)
            self.fields[f"profesional_item_{item.pk}"] = forms.ChoiceField(
                choices=[(k, v) for k, v in opciones.items()],
                initial=item.id_profesional,
                required=False,
            )
            # FR-016: cada control del ítem se anuncia con su referencia.
            ref = item.referencia
            self.fields[f"monto_item_{item.pk}"].widget.attrs["aria-label"] = (
                f"Monto del ítem {ref}"
            )
            self.fields[f"profesional_item_{item.pk}"].widget.attrs["aria-label"] = (
                f"Profesional del ítem {ref}"
            )
            self.filas.append(
                {
                    "item": item,
                    "monto": self[f"monto_item_{item.pk}"],
                    "profesional": self[f"profesional_item_{item.pk}"],
                }
            )

    @staticmethod
    def valores(cobro):
        return {
            "fecha": cobro.fecha.astimezone().replace(second=0, microsecond=0),
            "monto": cobro.monto,
            "metodo": cobro.metodo,
            "cuenta": cobro.cuenta_id if cobro.metodo == Cobro.Metodo.TRANSFERENCIA else None,
            "terminal": cobro.terminal_id,
            "monto_neto": cobro.monto_neto,
            "numero_operacion": cobro.numero_operacion,
            "banco_emisor": cobro.banco_emisor_id,
            "ultimos_4": cobro.ultimos_4,
            "observaciones": cobro.observaciones,
            "estado_visto": cobro.estado,
        }

    def cambios(self):
        """Solo los campos del cobro que el administrador cambió."""
        d, c = self.cleaned_data, self.cobro
        antes = {
            "fecha": c.fecha.replace(second=0, microsecond=0),  # el campo es de minutos
            "cuenta": c.cuenta if c.metodo == Cobro.Metodo.TRANSFERENCIA else None,
        }
        resultado = {}
        for campo in (
            "fecha",
            "monto",
            "metodo",
            "terminal",
            "monto_neto",
            "numero_operacion",
            "banco_emisor",
            "ultimos_4",
            "observaciones",
            "cuenta",
        ):
            if campo not in d:
                continue
            nuevo = d[campo]
            if campo in ("numero_operacion", "ultimos_4"):
                nuevo = nuevo or None
            if campo == "observaciones" and nuevo == c.observaciones:
                continue
            if nuevo != antes.get(campo, getattr(c, campo)):
                if campo == "cuenta" and nuevo is None:
                    continue  # sin elegir cuenta: el servicio decide según el método
                resultado[campo] = nuevo
        return resultado

    def clean_observaciones(self):
        texto = self.cleaned_data["observaciones"].replace("\r\n", "\n")
        # Una observación vieja de más de 1.000 se muestra entera y se acepta sin cambios.
        anterior = (self.cobro.observaciones or "").strip().replace("\r\n", "\n")
        if len(texto) > MAX_OBSERVACIONES and texto != anterior:
            raise forms.ValidationError(MENSAJE_OBSERVACIONES)
        return texto

    def items(self):
        d = self.cleaned_data
        resultado = []
        for fila in self.filas:
            i = fila["item"]
            profesional = d.get(f"profesional_item_{i.pk}")
            profesional = int(profesional) if profesional else i.id_profesional
            if (d[f"monto_item_{i.pk}"], profesional) != (i.monto, i.id_profesional):
                resultado.append(
                    {"id": i.pk, "monto": d[f"monto_item_{i.pk}"], "id_profesional": profesional}
                )
        if d["nuevo_prestacion"] and d["nuevo_monto"] is not None:
            resultado.append(
                {
                    "id_prestacion": int(d["nuevo_prestacion"]),
                    "monto": d["nuevo_monto"],
                    "id_profesional": int(d["nuevo_profesional"])
                    if d["nuevo_profesional"]
                    else None,
                }
            )
        return resultado


class _SelectCuenta(forms.Select):
    """`data-destinatario` en cada opción: Alpine muestra solo las del destinatario elegido (FR-028)."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opcion = super().create_option(name, value, label, selected, index, subindex, attrs)
        cuenta = getattr(value, "instance", None)
        if cuenta is not None:
            opcion["attrs"]["data-destinatario"] = cuenta.destinatario_id
        return opcion


class PagoComplementarioForm(forms.Form):
    """Alta y edición de un pago complementario. Sin reglas de negocio: valida el servicio."""

    destinatario = forms.ModelChoiceField(Destinatario.objects.none())
    mes = MesField()
    monto = MontoDecimalField(**MONTO)
    metodo = forms.ChoiceField(choices=PagoComplementario.Metodo.choices, label="Método")
    cuenta = forms.ModelChoiceField(Cuenta.objects.none(), required=False, widget=_SelectCuenta)
    comprobante = forms.FileField(required=False, label="Comprobante")
    observaciones = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        visibles = Destinatario.objects.visibles()
        if instance:
            visibles = Destinatario.objects.filter(
                pk__in=[instance.destinatario_id, *visibles.values_list("pk", flat=True)]
            ).order_by("nombre")
        self.fields["destinatario"].queryset = visibles
        # FR-028: todas las cuentas Activas de destinatarios visibles (la del pago que se edita, siempre)
        cuentas = Cuenta.objects.filter(tipo="transferencia").select_related("destinatario")
        activas = Q(estado="activa", destinatario__in=Destinatario.objects.visibles())
        cuentas = cuentas.filter(activas | Q(pk=instance.cuenta_id) if instance else activas)
        self.fields["cuenta"].queryset = cuentas.para_selector()
        self.fields["cuenta"].label_from_instance = lambda c: c.etiqueta_selector
        # Un destinatario con 1 sola cuenta: viene elegida
        inicial = self.initial.get("destinatario")
        if not self.is_bound and inicial and not self.initial.get("cuenta"):
            propias = [c for c in cuentas if str(c.destinatario_id) == str(inicial)]
            if len(propias) == 1:
                self.initial["cuenta"] = propias[0].pk


class AnularPagoForm(forms.Form):
    motivo = forms.ChoiceField(choices=PagoComplementario.MotivoAnulacion.choices)
    detalle = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
