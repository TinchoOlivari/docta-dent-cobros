from django.urls import path

from . import views

app_name = "cobros"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.nuevo, name="nuevo"),
    path("complementarios/", views.complementarios, name="complementarios"),
    path("complementarios/nuevo/", views.complementario_nuevo, name="complementario_nuevo"),
    path("complementarios/<int:pk>/", views.complementario_detalle, name="complementario_detalle"),
    path(
        "complementarios/<int:pk>/editar/",
        views.complementario_editar,
        name="complementario_editar",
    ),
    path(
        "complementarios/<int:pk>/anular/",
        views.complementario_anular,
        name="complementario_anular",
    ),
    path(
        "complementarios/<int:pk>/comprobante/",
        views.complementario_comprobante,
        name="complementario_comprobante",
    ),
    path("revisado-lote/", views.revisado_lote, name="revisado_lote"),
    path("cancelar-lote/", views.cancelar_lote, name="cancelar_lote"),
    path("<int:pk>/", views.detalle, name="detalle"),
    path("<int:pk>/adjuntar/", views.adjuntar, name="adjuntar"),
    path("<int:pk>/observaciones/", views.observaciones, name="observaciones"),
    path("<int:pk>/reabrir/", views.reabrir, name="reabrir"),
    path("comprobantes/<int:pk>/", views.comprobante_otro, name="comprobante_otro"),
    path("<int:pk>/cancelar/", views.cancelar, name="cancelar"),
    path(
        "<int:pk>/registrar-transferencia/",
        views.registrar_transferencia,
        name="registrar_transferencia",
    ),
    path("<int:pk>/empezar-de-nuevo/", views.empezar_de_nuevo, name="empezar_de_nuevo"),
    path("<int:pk>/items/", views.items, name="items"),
    path("<int:pk>/editar/", views.editar, name="editar"),
    path("<int:pk>/revisado/", views.revisado, name="revisado"),
    path("<int:pk>/comprobante/", views.comprobante, name="comprobante"),
]
