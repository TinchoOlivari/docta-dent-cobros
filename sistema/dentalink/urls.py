from django.urls import path

from . import views

app_name = "dentalink"
urlpatterns = [
    path("pacientes/", views.pacientes, name="pacientes"),
    path("pacientes/<int:id_paciente>/", views.paciente, name="paciente"),
    path("configuracion/", views.configuracion, name="configuracion"),
    path("configuracion/actualizar/", views.actualizar, name="actualizar"),
    path("envios/", views.envios, name="envios"),
    path("envios/<int:pk>/<slug:accion>/", views.accion, name="accion"),
]
