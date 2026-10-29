from django import template
from django.utils.timesince import timesince as django_timesince

register = template.Library()


@register.filter
def timesince_short(value, arg=None):
    """`timesince` con una sola unidad: "hace 3 horas", nunca "3 horas, 15 minutos"."""
    resultado = django_timesince(value, arg, depth=1)
    return resultado.replace("minutos", "mins").replace("minuto", "min")
