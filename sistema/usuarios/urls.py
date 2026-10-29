from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views
from .forms import LoginForm

app_name = "usuarios"
urlpatterns = [
    path(
        "login/",
        LoginView.as_view(
            authentication_form=LoginForm,
            template_name="usuarios/login.html",
            redirect_authenticated_user=True,
            next_page="usuarios:inicio",
        ),
        name="login",
    ),
    path("salir/", LogoutView.as_view(next_page="usuarios:login"), name="logout"),
    path("", views.inicio, name="inicio"),
    path("panel/", views.panel, name="panel"),
    path("operador/", views.operador, name="operador"),
    path("profesional/", views.profesional, name="profesional"),
    path("usuarios/", views.lista, name="lista"),
    path("usuarios/nuevo/", views.nuevo, name="nuevo"),
    path("usuarios/<int:pk>/editar/", views.editar, name="editar"),
    path("usuarios/<int:pk>/activo/", views.activo, name="activo"),
    path("usuarios/<int:pk>/desbloquear/", views.desbloquear, name="desbloquear"),
    path("usuarios/<int:pk>/contrasena/", views.contrasena, name="contrasena"),
]
