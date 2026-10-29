from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from sistema.auditoria.models import Evento
from sistema.auditoria.servicios import BLOQUEADO, MAX_FALLOS, fallos_seguidos, registrar


@receiver(user_logged_in)
def al_entrar(sender, request, user, **kwargs):
    registrar(Evento.Tipo.INICIO_SESION, usuario=user, entidad=user)


@receiver(user_logged_out)
def al_salir(sender, request, user, **kwargs):
    if user is not None:
        registrar(Evento.Tipo.CIERRE_SESION, usuario=user, entidad=user)


@receiver(user_login_failed)
def al_fallar(sender, credentials, request=None, **kwargs):
    # Nunca se guarda credentials["password"] (FR-010).
    username = str(credentials.get("username", ""))[:150]
    user = get_user_model()._default_manager.filter(username=username).first()
    bloqueado = getattr(request, "login_bloqueado", False)
    registrar(
        Evento.Tipo.LOGIN_FALLIDO,
        usuario=user,
        entidad=user,
        usuario_intentado=username,
        detalle=BLOQUEADO if bloqueado else "",
    )
    # ponytail: 2 fallos simultáneos pueden grabar 2 bloqueos; inofensivo
    if user and not bloqueado and fallos_seguidos(user) >= MAX_FALLOS:
        registrar(
            Evento.Tipo.BLOQUEO, usuario=None, entidad=user, origen="5 logins fallidos seguidos"
        )
