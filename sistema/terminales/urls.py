from django.urls import path

from . import views

app_name = "terminales"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nueva/", views.nueva, name="nueva"),
    path("<int:pk>/", views.detalle, name="detalle"),
    path("<int:pk>/editar/", views.editar, name="editar"),
    path("<int:pk>/estado/", views.estado, name="estado"),
    path("<int:pk>/abrir/", views.abrir, name="abrir"),
    path("<int:pk>/cambiar-cuenta/", views.cambiar_cuenta, name="cambiar_cuenta"),
    path("sesiones/<int:pk>/cerrar/", views.cerrar, name="cerrar"),
    path("sesiones/<int:pk>/editar/", views.editar_sesion, name="editar_sesion"),
    path("sesiones/<int:pk>/corregir/", views.corregir, name="corregir"),
    path("sesiones/<int:pk>/borrar/", views.borrar_sesion, name="borrar_sesion"),
    path("cuentas/", views.cuentas, name="cuentas"),
    path("cuentas/<int:pk>/acumula-desde/", views.acumula_desde, name="acumula_desde"),
]
