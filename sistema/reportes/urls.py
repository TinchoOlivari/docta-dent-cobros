from django.urls import path

from . import views

app_name = "reportes"
urlpatterns = [
    path("destinatarios/<int:pk>/", views.destinatario, name="destinatario"),
    path("destinatarios/descargar/", views.destinatarios_descargar, name="destinatarios_descargar"),
    path("cobros/descargar/", views.cobros_descargar, name="cobros_descargar"),
    path("cobros/elegidos/", views.cobros_elegidos, name="cobros_elegidos"),
]
