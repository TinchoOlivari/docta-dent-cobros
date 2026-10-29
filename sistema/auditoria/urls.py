from django.urls import path

from . import views

app_name = "auditoria"
urlpatterns = [
    path("", views.eventos, name="eventos"),
    path("<str:entidad>/<int:pk>/", views.historial, name="historial"),
]
