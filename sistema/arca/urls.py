from django.urls import path

from . import views

app_name = "arca"
urlpatterns = [
    path("emisores/", views.emisores, name="emisores"),
    path("emisores/nuevo/", views.nuevo_emisor, name="nuevo_emisor"),
    path("emisores/<int:pk>/", views.emisor, name="emisor"),
    path("emisores/<int:pk>/estado/", views.estado_emisor, name="estado_emisor"),
    path("cobros/<int:pk>/factura/", views.datos_factura, name="datos_factura"),
    path("cobros/<int:pk>/nota-credito/", views.nota_credito, name="nota_credito"),
    path("cobros/<int:pk>/revisar/", views.revisar, name="revisar"),
    path("comprobantes/<int:pk>/pdf/", views.pdf, name="pdf"),
    path("comprobantes/<int:pk>/reintentar/", views.reintentar, name="reintentar"),
    path("libro/", views.libro, name="libro"),
    path("libro/descargar/", views.libro_descargar, name="libro_descargar"),
]
