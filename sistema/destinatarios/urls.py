from django.urls import path

from . import views

app_name = "destinatarios"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.nuevo, name="nuevo"),
    path("<int:pk>/", views.detalle, name="detalle"),
    path("<int:pk>/editar/", views.editar, name="editar"),
    path("<int:pk>/cuentas/nueva/<str:tipo>/", views.nueva_cuenta, name="nueva_cuenta"),
    path("<int:pk>/estado/", views.estado_destinatario, name="estado_destinatario"),
    path("<int:pk>/desactivar-cuentas/", views.desactivar_cuentas, name="desactivar_cuentas"),
    path("estado-lote/", views.estado_lote, name="estado_lote"),
    path("proveedores/", views.proveedores, name="proveedores"),
    path("cuentas/<int:pk>/editar/", views.editar_cuenta, name="editar_cuenta"),
    path("cuentas/<int:pk>/estado/", views.estado_cuenta, name="estado_cuenta"),
]
