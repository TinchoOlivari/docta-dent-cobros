from django.urls import include, path

urlpatterns = [
    path("_ui/", include("sistema.core.urls")),
    path("destinatarios/", include("sistema.destinatarios.urls")),
    path("terminales/", include("sistema.terminales.urls")),
    path("asignacion/", include("sistema.asignacion.urls")),
    path("automatizacion/", include("sistema.automatizacion.urls")),
    path("avisos/", include("sistema.avisos.urls")),
    path("auditoria/", include("sistema.auditoria.urls")),
    path("cobros/", include("sistema.cobros.urls")),
    path("dentalink/", include("sistema.dentalink.urls")),
    path("reportes/", include("sistema.reportes.urls")),
    path("arca/", include("sistema.arca.urls")),
    path("", include("sistema.usuarios.urls")),
]
