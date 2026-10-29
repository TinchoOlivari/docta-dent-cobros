from django.urls import path

from . import views

app_name = "asignacion"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("<int:pk>/mover/", views.mover, name="mover"),
]
