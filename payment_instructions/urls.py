from django.urls import path
from . import views

app_name = "payment_instructions"

urlpatterns = [
    # Operator interface
    path("", views.operator_dashboard, name="operator_dashboard"),
    path("login/", views.operator_login, name="operator_login"),
    path("logout/", views.operator_logout, name="operator_logout"),
    path("crear-pago/", views.payment_method_selection, name="payment_method_selection"),
    path("transferencias-pendientes/", views.pending_transfers, name="pending_transfers"),
    # Payment forms
    path(
        "pago-comun/",
        views.create_transfer_payment_form,
        name="create_regular_payment_form",
    ),
    path(
        "pago-comun/<int:payment_id>/",
        views.create_transfer_payment_form,
        name="resume_transfer_payment_form",
    ),
    path("pago-efectivo/", views.create_cash_payment_form, name="create_cash_payment_form"),
    path("pago-tarjeta/", views.create_card_payment_form, name="create_card_payment_form"),
    path("pago-qr/", views.create_qr_payment_form, name="create_qr_payment_form"),
    path("pago-link/", views.create_link_payment_form, name="create_link_payment_form"),
    # AJAX endpoints
    path("iniciar-transferencia/", views.iniciar_transferencia, name="iniciar_transferencia"),
    path("completar-pago/", views.complete_payment, name="complete_payment"),
    path("cancelar-pago/", views.cancel_payment, name="cancel_payment"),
    path("create-deposit/", views.create_deposit, name="create_deposit"),
    path("create-cash-payment/", views.create_cash_payment, name="create_cash_payment"),
    path("create-card-payment/", views.create_card_payment, name="create_card_payment"),
    path("create-qr-payment/", views.create_qr_payment, name="create_qr_payment"),
    path("create-link-payment/", views.create_link_payment, name="create_link_payment"),
]
