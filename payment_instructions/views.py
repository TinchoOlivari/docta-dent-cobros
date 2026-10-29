from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json
import logging

from payment_instructions.utils.file_compression import FileCompressor
from .utils.validate_payment_amount import validate_payment_amount
from .models import (
    Banco,
    Payment,
    PaymentRecipient,
    Posnet,
    PosnetPaymentDetail,
    TarjetaPaymentDetail,
    User,
)

# Contrato de error de los endpoints AJAX:
#   {"error": "mensaje en español"}                  -> aviso general, el front lo muestra
#                                                       en el snackbar flotante.
#   {"error": "...", "field": "<parámetro POST>"}    -> culpa de un campo: el front marca
#                                                       ese input y escribe el error debajo.
# El nombre de "field" es el del parámetro POST (amount, patient_name, ...), no el id del
# input: los cinco formularios usan ids distintos para el mismo dato y cada uno traduce.
# Un 500 nunca lleva "field": no hay campo que corregir.

# Comprobante: mismos límites en los tres formularios que lo aceptan.
MAX_PROOF_BYTES = 5 * 1024 * 1024
PROOF_FIELD = "proof_of_payment_file"

# Lo devuelve validate_payment_amount y también la view cuando la carrera por el cupo se
# queda sin candidatos. Va sin "field": no hay input que corregir.
SIN_DESTINATARIOS = "No hay destinatarios disponibles para este monto"
ALLOWED_PROOF_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/gif", "application/pdf"}

logger = logging.getLogger(__name__)


def _validate_patient_name(raw_patient_name):
    """Normaliza y valida el nombre de paciente.

    Devuelve (nombre, None) si es válido, o (None, JsonResponse) con el error listo
    para devolver tal cual desde la view."""
    patient_name = (raw_patient_name or "").strip()
    if not patient_name:
        return None, JsonResponse(
            {"error": "Nombre del paciente es requerido.", "field": "patient_name"}, status=400
        )
    letter_count = sum(1 for c in patient_name if c.isalpha())
    if letter_count < 3:
        return None, JsonResponse(
            {"error": "El nombre del paciente debe ser mas largo.", "field": "patient_name"},
            status=400,
        )
    return patient_name, None


def _get_professional_or_error(request, professional_id):
    """Busca el profesional activo por id.

    Devuelve (User, None) si existe, o (None, JsonResponse) con el error listo para
    devolver tal cual desde la view."""
    if not professional_id:
        logger.warning(
            "Profesional no encontrado: professional_id=%s user=%s path=%s",
            professional_id,
            getattr(request.user, "username", None),
            request.path,
        )
        return None, JsonResponse(
            {"error": "Profesional es requerido", "field": "professional_id"}, status=400
        )

    try:
        professional_id_int = int(str(professional_id))
    except (TypeError, ValueError):
        logger.warning(
            "ID de profesional inválido: professional_id=%s user=%s path=%s",
            professional_id,
            getattr(request.user, "username", None),
            request.path,
        )
        return None, JsonResponse(
            {"error": "ID de profesional inválido", "field": "professional_id"}, status=400
        )

    try:
        professional = User.objects.get(
            pk=professional_id_int, role=User.PROFESSIONAL, is_active=True
        )
    except User.DoesNotExist:
        logger.warning(
            "Profesional no encontrado: professional_id=%s user=%s path=%s",
            professional_id,
            getattr(request.user, "username", None),
            request.path,
        )
        return None, JsonResponse(
            {"error": "Profesional no encontrado", "field": "professional_id"}, status=400
        )

    return professional, None


def _get_active_professionals():
    """Profesionales activos, para poblar el select de los tres forms de pago."""
    return User.objects.filter(role=User.PROFESSIONAL, is_active=True).order_by("username")


def _validate_and_compress_proof(request, file_obj):
    """Valida tamaño y tipo del comprobante y lo comprime.

    Devuelve (archivo_comprimido, None) si está bien, o (None, JsonResponse) con el
    error listo para devolver tal cual desde la view."""
    if file_obj.size > MAX_PROOF_BYTES:
        return None, JsonResponse(
            {"error": "El archivo es demasiado grande. Máximo 5MB.", "field": PROOF_FIELD},
            status=400,
        )

    if file_obj.content_type not in ALLOWED_PROOF_TYPES:
        return None, JsonResponse(
            {"error": "Tipo de archivo no válido. Solo imágenes o PDF.", "field": PROOF_FIELD},
            status=400,
        )

    try:
        return FileCompressor.compress_file(file_obj), None
    except Exception:
        logger.warning(
            "Error al comprimir el archivo adjunto: user=%s path=%s",
            getattr(request.user, "username", None),
            request.path,
        )
        return None, JsonResponse(
            {
                "error": "Error al comprimir el archivo adjunto. Utilizar una imagen o PDF.",
                "field": PROOF_FIELD,
            },
            status=400,
        )


def _parse_montos(monto_cobrado_raw, monto_recibido_raw):
    """Valida el par de montos de los formularios de posnet (tarjeta, QR, link).

    Devuelve (monto_cobrado, monto_recibido, None) si están bien, o (None, None,
    JsonResponse) con el error listo. Valida uno por vez para poder decir cuál de los
    dos campos está mal: un solo error para los dos deja al operador adivinando."""
    montos = {}
    for field, raw in (
        ("monto_cobrado", monto_cobrado_raw),
        ("monto_recibido", monto_recibido_raw),
    ):
        try:
            montos[field] = int(str(raw))
        except (TypeError, ValueError):
            return (
                None,
                None,
                JsonResponse({"error": "Formato de monto inválido", "field": field}, status=400),
            )
        if montos[field] <= 0:
            return (
                None,
                None,
                JsonResponse(
                    {"error": "El monto debe ser mayor a cero", "field": field}, status=400
                ),
            )

    if montos["monto_cobrado"] < montos["monto_recibido"]:
        return (
            None,
            None,
            JsonResponse(
                {
                    "error": "El monto cobrado no puede ser menor al monto recibido.",
                    "field": "monto_cobrado",
                },
                status=400,
            ),
        )

    return montos["monto_cobrado"], montos["monto_recibido"], None


def require_payment_creation_permission(view_func):
    """Decorator to ensure only administrators and operators can create payments"""

    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("payment_instructions:operator_login")

        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        if hasattr(request.user, "role") and request.user.role in [
            User.ADMINISTRATOR,
            User.OPERATOR,
        ]:
            return view_func(request, *args, **kwargs)

        if hasattr(request.user, "role") and request.user.role in [User.PROFESSIONAL]:
            return redirect("admin/")

        return redirect("/")

    return wrapper


def operator_login(request):
    """Login view for operators"""
    if request.user.is_authenticated:
        return redirect("payment_instructions:operator_dashboard")

    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("payment_instructions:operator_dashboard")
        else:
            messages.error(request, "Usuario o contraseña incorrectos")

    return render(request, "payment_instructions/login.html")


def operator_logout(request):
    """Logout view"""
    logout(request)
    return redirect("payment_instructions:operator_login")


@login_required
@require_payment_creation_permission
def operator_dashboard(request):
    """Home: dos botones grandes, crear pago o ver transferencias pendientes."""
    return render(
        request,
        "payment_instructions/dashboard.html",
        {"pendientes_count": _pending_transfers_queryset(request.user).count()},
    )


def _pending_transfers_queryset(user):
    """Pagos pendientes que este usuario puede ver: los propios siempre, todos si es
    administrador. Mismo criterio en el contador del dashboard y en la lista."""
    qs = Payment.objects.filter(estado=Payment.PENDIENTE)
    if not _is_administrator(user):
        qs = qs.filter(operator_user=user)
    return qs


@login_required
@require_payment_creation_permission
def payment_method_selection(request):
    """Pantalla de selección de método de pago (transferencia, efectivo, ...)."""
    return render(request, "payment_instructions/payment_method_selection.html")


def _is_administrator(user):
    return user.is_superuser or (hasattr(user, "role") and user.role == User.ADMINISTRATOR)


@login_required
@require_payment_creation_permission
def create_transfer_payment_form(request, payment_id=None):
    """Form de transferencia: toggle Regular/Seña. Si viene payment_id, precarga un
    pago PENDIENTE propio para completarlo (retomar desde la lista de transferencias
    pendientes)."""
    professionals = _get_active_professionals()
    deposit_recipient = PaymentRecipient.get_deposit_recipient()

    pendiente = None
    pendiente_not_found = False
    if payment_id:
        # Mismo criterio que complete_payment y cancel_payment: el operador retoma lo
        # propio, el administrador cualquiera. Si esta pantalla filtrara más fuerte que
        # esos dos endpoints, un administrador vería "ya no está pendiente" sobre un
        # pago que igual puede completar o cancelar.
        pendiente = (
            _pending_transfers_queryset(request.user)
            .select_related("payment_recipient", "professional")
            .filter(pk=payment_id)
            .first()
        )
        # Ya completado, cancelado, o de otro operador: no hay nada que retomar.
        if pendiente is None:
            pendiente_not_found = True

    return render(
        request,
        "payment_instructions/regular_payment_form.html",
        {
            "professionals": professionals,
            "deposit_recipient": deposit_recipient,
            "pendiente": pendiente,
            "pendiente_not_found": pendiente_not_found,
            "motivos_cancelacion": Payment.MOTIVO_CANCELACION_CHOICES,
        },
    )


@login_required
@require_payment_creation_permission
def create_cash_payment_form(request):
    """Form to create a cash payment (regular o seña, sin destinatario ni comprobante)"""
    professionals = _get_active_professionals()
    return render(
        request,
        "payment_instructions/cash_payment_form.html",
        {
            "professionals": professionals,
        },
    )


@login_required
@require_payment_creation_permission
@require_http_methods(["POST"])
def create_cash_payment(request):
    """Create a new cash payment (efectivo, regular o seña). Sin destinatario, sin
    comprobante y sin chequeo de tope: el efectivo no compite por un cupo compartido."""
    try:
        amount = request.POST.get("amount")
        patient_name_raw = request.POST.get("patient_name", "")
        professional_id = request.POST.get("professional_id")
        observaciones = request.POST.get("observaciones")
        es_seña = request.POST.get("payment_type") == Payment.DEPOSIT

        if not amount:
            return JsonResponse({"error": "Monto es requerido", "field": "amount"}, status=400)

        try:
            amount_decimal = int(str(amount))
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "Formato de monto inválido", "field": "amount"}, status=400
            )
        if amount_decimal <= 0:
            return JsonResponse(
                {"error": "El monto debe ser mayor a cero", "field": "amount"}, status=400
            )

        patient_name, error = _validate_patient_name(patient_name_raw)
        if error:
            return error

        professional, error = _get_professional_or_error(request, professional_id)
        if error:
            return error

        # Todo lo que puede fallar por dato del usuario ya se validó arriba: si
        # Payment.objects.create() falla acá es un error del servidor, lo agarra el
        # except de más abajo (logger.exception, 500, llega a Sentry).
        payment = Payment.objects.create(
            amount=amount_decimal,
            payment_type=Payment.DEPOSIT if es_seña else Payment.REGULAR,
            metodo=Payment.EFECTIVO,
            # La plata está en la mano: no hay comprobante que esperar.
            estado=Payment.COMPLETADO,
            patient_name=patient_name,
            professional=professional,
            operator_user=request.user,
            observaciones=observaciones,
        )

        return JsonResponse(
            {
                "success": True,
                "payment_id": payment.id,
                "message": f"Pago en efectivo creado exitosamente. (id: {payment.id})",
            }
        )

    except Exception:
        # exception, no warning: esto devuelve 500 y tiene que llegar a Sentry.
        logger.exception(
            "Error interno del servidor: user=%s path=%s",
            getattr(request.user, "username", None),
            request.path,
        )
        return JsonResponse({"error": "Error interno del servidor"}, status=500)


@login_required
@require_payment_creation_permission
def create_card_payment_form(request):
    """Form to create a card payment (posnet, sin destinatario ni comprobante)."""
    return render(
        request,
        "payment_instructions/card_payment_form.html",
        {
            "professionals": _get_active_professionals(),
            "bancos": Banco.objects.filter(is_active=True).order_by("nombre"),
            "posnets": Posnet.objects.filter(
                Q(acepta_credito=True) | Q(acepta_debito=True), is_active=True
            ).order_by("nombre"),
            "tipo_tarjeta_choices": TarjetaPaymentDetail.TIPO_TARJETA_CHOICES,
        },
    )


@login_required
@require_payment_creation_permission
@require_http_methods(["POST"])
def create_card_payment(request):
    """Create a new card payment (posnet). Sin destinatario: este método no compite
    por un cupo compartido, la plata la recibe la cuenta logeada en el posnet."""
    try:
        monto_cobrado_raw = request.POST.get("monto_cobrado")
        monto_recibido_raw = request.POST.get("monto_recibido")
        tipo_tarjeta = request.POST.get("tipo_tarjeta")
        banco_id = request.POST.get("banco_id")
        posnet_id = request.POST.get("posnet_id")
        ultimos_4_digitos = (request.POST.get("ultimos_4_digitos") or "").strip()
        numero_operacion = (request.POST.get("numero_operacion") or "").strip()
        patient_name_raw = request.POST.get("patient_name", "")
        professional_id = request.POST.get("professional_id")
        observaciones = request.POST.get("observaciones")

        monto_cobrado, monto_recibido, error = _parse_montos(monto_cobrado_raw, monto_recibido_raw)
        if error:
            return error

        patient_name, error = _validate_patient_name(patient_name_raw)
        if error:
            return error

        professional, error = _get_professional_or_error(request, professional_id)
        if error:
            return error

        if tipo_tarjeta not in dict(TarjetaPaymentDetail.TIPO_TARJETA_CHOICES):
            return JsonResponse(
                {"error": "Tipo de tarjeta inválido.", "field": "tipo_tarjeta"}, status=400
            )

        banco = Banco.objects.filter(pk=banco_id, is_active=True).first()
        if not banco:
            return JsonResponse(
                {"error": "Banco no encontrado o inactivo.", "field": "banco_id"}, status=400
            )

        posnet = Posnet.objects.filter(pk=posnet_id, is_active=True).first()
        if not posnet:
            return JsonResponse(
                {"error": "Posnet no encontrado o inactivo.", "field": "posnet_id"}, status=400
            )

        acepta_tipo = (
            posnet.acepta_credito
            if tipo_tarjeta == TarjetaPaymentDetail.CREDITO
            else posnet.acepta_debito
        )
        if not acepta_tipo:
            tipo_label = dict(TarjetaPaymentDetail.TIPO_TARJETA_CHOICES)[tipo_tarjeta]
            return JsonResponse(
                {"error": f"Este posnet no acepta {tipo_label}.", "field": "posnet_id"}, status=400
            )

        if posnet.cuenta_actual_id is None or not posnet.cuenta_actual.is_active:
            return JsonResponse(
                {
                    "error": "Este posnet no tiene una cuenta logeada. Contactá al administrador.",
                    "field": "posnet_id",
                },
                status=400,
            )

        if not ultimos_4_digitos.isdigit() or len(ultimos_4_digitos) != 4:
            return JsonResponse(
                {
                    "error": "Deben ser 4 números.",
                    "field": "ultimos_4_digitos",
                },
                status=400,
            )

        if not numero_operacion:
            return JsonResponse(
                {"error": "Número de operación es requerido.", "field": "numero_operacion"},
                status=400,
            )

        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    amount=monto_recibido,
                    payment_type=Payment.REGULAR,
                    metodo=Payment.POSNET_TARJETA,
                    estado=Payment.COMPLETADO,
                    patient_name=patient_name,
                    professional=professional,
                    operator_user=request.user,
                    observaciones=observaciones,
                )
                TarjetaPaymentDetail.objects.create(
                    payment=payment,
                    tipo_tarjeta=tipo_tarjeta,
                    banco=banco,
                    ultimos_4_digitos=ultimos_4_digitos,
                    posnet=posnet,
                    cuenta=posnet.cuenta_actual,
                    numero_operacion=numero_operacion,
                    monto_cobrado=monto_cobrado,
                )
        except IntegrityError:
            # Doble click o reenvío del mismo cobro: mismo posnet + mismo número de
            # operación ya se cargó. No es un error del servidor, es un duplicado.
            return JsonResponse(
                {
                    "error": (
                        "Ya existe un pago cargado con este número de operación en este posnet."
                    ),
                    "field": "numero_operacion",
                },
                status=400,
            )

        return JsonResponse(
            {
                "success": True,
                "payment_id": payment.id,
                "message": f"Pago con tarjeta creado exitosamente. (id: {payment.id})",
            }
        )

    except Exception:
        # exception, no warning: esto devuelve 500 y tiene que llegar a Sentry.
        logger.exception(
            "Error interno del servidor: user=%s path=%s",
            getattr(request.user, "username", None),
            request.path,
        )
        return JsonResponse({"error": "Error interno del servidor"}, status=500)


@login_required
@require_payment_creation_permission
def create_qr_payment_form(request):
    """Form de pago con QR (posnet, sin destinatario, comprobante opcional)."""
    return render(
        request,
        "payment_instructions/qr_payment_form.html",
        {
            "professionals": _get_active_professionals(),
            "posnets": Posnet.objects.filter(is_active=True, acepta_qr=True).order_by("nombre"),
        },
    )


@login_required
@require_payment_creation_permission
@require_http_methods(["POST"])
def create_qr_payment(request):
    """Crea un pago con QR. Paga en el momento, frente al operador: el comprobante
    es opcional, la aprobación del posnet ya alcanza."""
    return _create_posnet_payment(request, Payment.POSNET_QR, comprobante_required=False)


@login_required
@require_payment_creation_permission
def create_link_payment_form(request):
    """Form de link de pago (posnet, sin destinatario, comprobante obligatorio)."""
    return render(
        request,
        "payment_instructions/link_payment_form.html",
        {
            "professionals": _get_active_professionals(),
            "posnets": Posnet.objects.filter(is_active=True, acepta_link_pago=True).order_by(
                "nombre"
            ),
        },
    )


@login_required
@require_payment_creation_permission
@require_http_methods(["POST"])
def create_link_payment(request):
    """Crea un pago por link de pago. Un solo paso, con comprobante obligatorio
    subido en la misma carga: nace COMPLETADO, no queda pendiente."""
    return _create_posnet_payment(request, Payment.LINK_PAGO, comprobante_required=True)


def _create_posnet_payment(request, metodo, comprobante_required):
    """Lógica común a QR y link de pago: mismo shape de datos (posnet, cuenta,
    número de operación, monto cobrado), sin destinatario ni cupo que cuidar.
    Solo cambia el método y si el comprobante es obligatorio."""
    try:
        monto_cobrado_raw = request.POST.get("monto_cobrado")
        monto_recibido_raw = request.POST.get("monto_recibido")
        posnet_id = request.POST.get("posnet_id")
        numero_operacion = (request.POST.get("numero_operacion") or "").strip()
        patient_name_raw = request.POST.get("patient_name", "")
        professional_id = request.POST.get("professional_id")
        observaciones = request.POST.get("observaciones")
        file_obj = request.FILES.get("proof_of_payment_file")

        monto_cobrado, monto_recibido, error = _parse_montos(monto_cobrado_raw, monto_recibido_raw)
        if error:
            return error

        patient_name, error = _validate_patient_name(patient_name_raw)
        if error:
            return error

        professional, error = _get_professional_or_error(request, professional_id)
        if error:
            return error

        posnet = Posnet.objects.filter(pk=posnet_id, is_active=True).first()
        if not posnet:
            return JsonResponse(
                {"error": "Posnet no encontrado o inactivo.", "field": "posnet_id"}, status=400
            )

        acepta_metodo = posnet.acepta_qr if metodo == Payment.POSNET_QR else posnet.acepta_link_pago
        if not acepta_metodo:
            return JsonResponse(
                {
                    "error": f"Este posnet no acepta {dict(Payment.METODO_CHOICES)[metodo]}.",
                    "field": "posnet_id",
                },
                status=400,
            )

        if posnet.cuenta_actual_id is None or not posnet.cuenta_actual.is_active:
            return JsonResponse(
                {
                    "error": "Este posnet no tiene una cuenta logeada. Contactá al administrador.",
                    "field": "posnet_id",
                },
                status=400,
            )

        if not numero_operacion:
            return JsonResponse(
                {"error": "Número de operación es requerido.", "field": "numero_operacion"},
                status=400,
            )

        if comprobante_required and not file_obj:
            return JsonResponse(
                {"error": "El comprobante es requerido.", "field": PROOF_FIELD}, status=400
            )

        compressed_file = None
        if file_obj:
            compressed_file, error = _validate_and_compress_proof(request, file_obj)
            if error:
                return error

        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    amount=monto_recibido,
                    payment_type=Payment.REGULAR,
                    metodo=metodo,
                    estado=Payment.COMPLETADO,
                    patient_name=patient_name,
                    professional=professional,
                    operator_user=request.user,
                    observaciones=observaciones,
                )
                if compressed_file:
                    payment.proof_of_payment_file = compressed_file
                    payment.save(update_fields=["proof_of_payment_file"])
                PosnetPaymentDetail.objects.create(
                    payment=payment,
                    posnet=posnet,
                    cuenta=posnet.cuenta_actual,
                    numero_operacion=numero_operacion,
                    monto_cobrado=monto_cobrado,
                )
        except IntegrityError:
            # Doble click o reenvío del mismo cobro: mismo posnet + mismo número de
            # operación ya se cargó. No es un error del servidor, es un duplicado.
            return JsonResponse(
                {
                    "error": (
                        "Ya existe un pago cargado con este número de operación en este posnet."
                    ),
                    "field": "numero_operacion",
                },
                status=400,
            )

        return JsonResponse(
            {
                "success": True,
                "payment_id": payment.id,
                "message": f"Pago por {payment.get_metodo_display()} creado exitosamente. (id: {payment.id})",
            }
        )

    except Exception:
        # exception, no warning: esto devuelve 500 y tiene que llegar a Sentry.
        logger.exception(
            "Error interno del servidor: user=%s path=%s",
            getattr(request.user, "username", None),
            request.path,
        )
        return JsonResponse({"error": "Error interno del servidor"}, status=500)


@login_required
@require_payment_creation_permission
@require_http_methods(["POST"])
def iniciar_transferencia(request):
    """Elige destinatario y crea el pago PENDIENTE, todo en la misma operación.

    Acá es donde el cupo queda tomado. No hay un momento en que el sistema muestre
    un alias sin haberlo ocupado ya: PaymentManager.crear_pendiente revalida el cupo
    bajo lock antes de crear. Por eso el paciente y el profesional se piden ANTES de
    llegar a este endpoint — el pago nace completo, sin borrador.

    Con un pendiente abierto la pantalla bloquea monto, paciente y profesional y esconde
    el botón de buscar: para cambiar cualquiera de esos datos hay que cancelar el pago,
    con su motivo. Por eso acá no hay ningún descarte implícito."""
    try:
        data = json.loads(request.body)
        amount = data.get("amount")

        if not amount:
            return JsonResponse({"error": "Monto requerido", "field": "amount"}, status=400)

        patient_name, error = _validate_patient_name(data.get("patient_name"))
        if error:
            return error

        professional, error = _get_professional_or_error(request, data.get("professional_id"))
        if error:
            return error

        # Validate and get suggested recipient
        is_valid, message, recipient = validate_payment_amount(amount)

        if not is_valid:
            # Tope, mínimo por pago o monto mal formado: todos hablan del monto que se
            # escribió, así que marcan ese campo. Quedarse sin destinatarios no: es
            # capacidad del sistema, no un dato mal cargado, y el operador no lo arregla
            # editando el formulario.
            if message == SIN_DESTINATARIOS:
                return JsonResponse({"error": message}, status=400)
            return JsonResponse({"error": message, "field": "amount"}, status=400)

        if not recipient:
            return JsonResponse({"error": SIN_DESTINATARIOS}, status=400)

        amount_int = int(str(amount))

        # crear_pendiente devuelve None cuando otro operador se llevó el cupo entre la
        # sugerencia y el lock. Eso no significa que no haya destinatario: significa que
        # no hay ESE. Se sigue por orden de prioridad, que es lo que habría hecho la
        # asignación automática si la carrera hubiera salido al revés. Sin esto, con
        # varias operadoras trabajando a la vez contra un destinatario casi lleno, la
        # segunda recibe un error donde el siguiente por prioridad tenía cupo de sobra.
        candidatos = [recipient] + [
            otro
            for otro in PaymentRecipient.objects.get_available_recipients(amount_int)
            if otro.pk != recipient.pk
        ]

        payment = None
        for candidato in candidatos:
            payment = Payment.objects.crear_pendiente(
                payment_recipient=candidato,
                amount=amount_int,
                operator_user=request.user,
                patient_name=patient_name,
                professional=professional,
                observaciones=data.get("observaciones") or None,
            )
            if payment is not None:
                recipient = candidato
                break

        if payment is None:
            logger.warning(
                "Ningún destinatario pudo tomar el monto: amount=%s user=%s path=%s",
                amount_int,
                getattr(request.user, "username", None),
                request.path,
            )
            return JsonResponse({"error": SIN_DESTINATARIOS}, status=400)

        return JsonResponse(
            {
                "success": True,
                "alias": recipient.alias,
                "name": recipient.name,
                "amount": str(amount),
                "cbu": recipient.cbu,
                "recipient_id": recipient.id,
                "payment_id": payment.id,
            }
        )

    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"error": "Formato de monto inválido", "field": "amount"}, status=400)
    except Exception:
        # exception, no warning: esto devuelve 500 y tiene que llegar a Sentry.
        logger.exception(
            "Error al buscar alias: user=%s path=%s",
            getattr(request.user, "username", None),
            request.path,
        )
        return JsonResponse({"error": "Error interno del servidor"}, status=500)


@login_required
@require_payment_creation_permission
@require_http_methods(["POST"])
def create_deposit(request):
    """Registra una seña por transferencia.

    No pasa por el flujo de búsqueda: la seña va siempre al único destinatario
    DEPOSIT_ONLY activo y no consume cupo, así que no hay nada que bloquear ni que
    esperar. El comprobante es obligatorio y la seña nace COMPLETADO."""
    try:
        # Validate that deposit recipient exists
        recipient = PaymentRecipient.get_deposit_recipient()
        if not recipient:
            logger.warning(
                "No deposit recipient configured: user=%s path=%s",
                getattr(request.user, "username", None),
                request.path,
            )
            return JsonResponse(
                {"error": "No hay destinatario configurado para señas. Contacte al administrador."},
                status=400,
            )

        amount = request.POST.get("amount")
        patient_name_raw = request.POST.get("patient_name", "")
        professional_id = request.POST.get("professional_id")
        file_obj = request.FILES.get("proof_of_payment_file")
        observaciones = request.POST.get("observaciones")

        if not amount:
            return JsonResponse({"error": "Monto es requerido", "field": "amount"}, status=400)

        try:
            amount_decimal = int(str(amount))
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "Formato de monto inválido", "field": "amount"}, status=400
            )
        if amount_decimal <= 0:
            return JsonResponse(
                {"error": "El monto debe ser mayor a cero", "field": "amount"}, status=400
            )

        patient_name, error = _validate_patient_name(patient_name_raw)
        if error:
            return error

        professional, error = _get_professional_or_error(request, professional_id)
        if error:
            return error

        if not file_obj:
            return JsonResponse(
                {"error": "El comprobante es requerido.", "field": PROOF_FIELD}, status=400
            )

        compressed_file, error = _validate_and_compress_proof(request, file_obj)
        if error:
            return error

        try:
            # Create deposit payment
            payment = Payment.objects.create(
                amount=amount_decimal,
                payment_type=Payment.DEPOSIT,
                estado=Payment.COMPLETADO,
                payment_recipient=recipient,
                patient_name=patient_name,
                professional=professional,
                operator_user=request.user,
                observaciones=observaciones,
            )
            payment.proof_of_payment_file = compressed_file
            payment.save(update_fields=["proof_of_payment_file"])
        except Exception as e:
            # Falló crear un pago. Sea del usuario o del servidor, se mira.
            logger.exception(
                "Error al crear la seña: user=%s path=%s",
                getattr(request.user, "username", None),
                request.path,
            )
            return JsonResponse({"error": f"Error al crear la seña: {str(e)}"}, status=400)

        return JsonResponse(
            {
                "success": True,
                "payment_id": payment.id,
                "message": f"Seña creada exitosamente. (id: {payment.id})",
            }
        )

    except (ValueError, TypeError, json.JSONDecodeError) as e:
        logger.warning(
            "No se pudo registrar la seña: user=%s path=%s error=%s",
            getattr(request.user, "username", None),
            request.path,
            str(e),
        )
        return JsonResponse({"error": f"No se pudo registrar la seña. Error: {str(e)}"}, status=400)
    except Exception as e:
        # exception, no warning: esto devuelve 500 y tiene que llegar a Sentry.
        logger.exception(
            "Error interno del servidor: user=%s path=%s",
            getattr(request.user, "username", None),
            request.path,
        )
        return JsonResponse({"error": f"Error interno del servidor: {str(e)}"}, status=500)


@login_required
@require_payment_creation_permission
@require_http_methods(["POST"])
def complete_payment(request):
    """Adjunta el comprobante a un pago PENDIENTE propio y lo da por cobrado.

    No revalida el tope: el cupo se tomó cuando se creó el pendiente, en el mismo
    momento en que se eligió el destinatario. Si el paciente transfirió un monto
    distinto, no se edita acá — se cancela este pago y se carga uno nuevo."""
    try:
        payment_id = request.POST.get("payment_id")
        file_obj = request.FILES.get("proof_of_payment_file")
        observaciones = request.POST.get("observaciones")

        payment, error = _get_own_pending_payment_or_error(request, payment_id)
        if error:
            return error

        if not file_obj:
            return JsonResponse(
                {"error": "El comprobante es requerido.", "field": PROOF_FIELD}, status=400
            )

        compressed_file, error = _validate_and_compress_proof(request, file_obj)
        if error:
            return error

        try:
            payment.completar(compressed_file, observaciones=observaciones)
        except ValidationError as e:
            logger.warning(
                "No se pudo completar el pago: payment_id=%s user=%s path=%s error=%s",
                payment_id,
                getattr(request.user, "username", None),
                request.path,
                str(e),
            )
            return _validation_error_response(e)

        return JsonResponse(
            {
                "success": True,
                "payment_id": payment.id,
                "message": f"Pago completado exitosamente. (id: {payment.id})",
            }
        )

    except Exception:
        # exception, no warning: esto devuelve 500 y tiene que llegar a Sentry.
        logger.exception(
            "Error interno del servidor: user=%s path=%s",
            getattr(request.user, "username", None),
            request.path,
        )
        return JsonResponse({"error": "Error interno del servidor"}, status=500)


def _validation_error_response(validation_error):
    """Traduce un ValidationError de modelo al JSON de error del operador.

    Toma el primer mensaje y, si Django lo ató a un campo concreto, lo manda como
    "field". Un error de __all__ va sin campo: no hay un input al que apuntar.

    Ojo: el nombre que sale de acá es el del campo del MODELO. Coincide con el
    parámetro POST en los datos del pago (amount, patient_name, proof_of_payment_file),
    pero no en los de cancelación: cancelar() levanta sobre motivo_cancelacion mientras
    el formulario manda motivo. Ningún template los mapea, así que el front no encuentra
    el input y cae al aviso flotante, que es la salida correcta para ese caso."""
    if hasattr(validation_error, "message_dict"):
        for campo, errores in validation_error.message_dict.items():
            if errores:
                payload = {"error": errores[0]}
                if campo != "__all__":
                    payload["field"] = campo
                return JsonResponse(payload, status=400)
    return JsonResponse({"error": "Datos inválidos."}, status=400)


def _get_own_pending_payment_or_error(request, payment_id):
    """Busca un pago PENDIENTE propio por id.

    Devuelve (Payment, None) si existe, o (None, JsonResponse) listo para devolver
    tal cual desde la view. Un administrador puede tocar cualquier pendiente; un
    operador, solo los suyos."""
    if not payment_id:
        return None, JsonResponse({"error": "payment_id es requerido"}, status=400)
    try:
        qs = Payment.objects.filter(pk=int(str(payment_id)), estado=Payment.PENDIENTE)
    except (TypeError, ValueError):
        return None, JsonResponse({"error": "Pago no encontrado"}, status=400)
    if not _is_administrator(request.user):
        qs = qs.filter(operator_user=request.user)
    payment = qs.first()
    if payment is None:
        return None, JsonResponse({"error": "Pago no encontrado"}, status=400)
    return payment, None


@login_required
@require_payment_creation_permission
@require_http_methods(["POST"])
def cancel_payment(request):
    """Cancela un pago pendiente propio. Libera el cupo, no borra el registro."""
    try:
        data = json.loads(request.body)
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"error": "Formato inválido"}, status=400)

    try:
        payment, error = _get_own_pending_payment_or_error(request, data.get("payment_id"))
        if error:
            return error

        try:
            payment.cancelar(
                motivo=data.get("motivo"),
                usuario=request.user,
                observacion=data.get("observacion"),
            )
        except ValidationError as e:
            return _validation_error_response(e)

        return JsonResponse({"success": True})
    except Exception:
        # exception, no warning: esto devuelve 500 y tiene que llegar a Sentry.
        logger.exception(
            "Error al cancelar el pago: user=%s path=%s",
            getattr(request.user, "username", None),
            request.path,
        )
        return JsonResponse({"error": "Error interno del servidor"}, status=500)


@login_required
@require_payment_creation_permission
def pending_transfers(request):
    """Transferencias pendientes: las propias siempre, todas si quien mira es
    administrador — mismo criterio que el resto del sistema.

    Orden del más viejo primero: un pendiente ocupa cupo hasta que alguien lo
    completa o lo cancela, así que lo que más urge revisar es lo que lleva más
    tiempo colgado."""
    qs = _pending_transfers_queryset(request.user).select_related(
        "payment_recipient", "professional", "operator_user"
    )

    return render(
        request,
        "payment_instructions/pending_transfers.html",
        {
            "pendientes": qs.order_by("created_at"),
            "own_operator_id": request.user.id,
            "puede_gestionar_ajenos": _is_administrator(request.user),
            "motivos_cancelacion": Payment.MOTIVO_CANCELACION_CHOICES,
        },
    )


def permission_denied(request, exception):
    """Custom 403 handler: show 'No tienes acceso a este recurso' instead of default 403 page."""
    return render(request, "admin/403.html", status=403)
