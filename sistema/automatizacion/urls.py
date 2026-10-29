from django.urls import path

from . import views

app_name = "automatizacion"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nueva/", views.nueva, name="nueva"),
    path("<int:pk>/editar/", views.editar, name="editar"),
    path("estado/", views.estado, name="estado"),
]
