from django.urls import path

from . import views

app_name = "avisos"

urlpatterns = [
    path("", views.configuracion, name="configuracion"),
    path("agregar/", views.agregar, name="agregar"),
    path("<int:pk>/borrar/", views.borrar, name="borrar"),
]
