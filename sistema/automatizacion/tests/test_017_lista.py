from datetime import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

from ..models import Regla
from . import ReglasTestCase

LISTA = reverse("automatizacion:lista")


class DescripcionYFiltrosTest(ReglasTestCase):
    def setUp(self):
        super().setUp()
        dest = crear_destinatario(usuario=None, nombre="Destinatario Ejemplo", tipo="profesional")
        self.uno = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="ejemplo.uno",
            tope_mensual=Decimal("1000000.00"),
            tope_semanal=Decimal("0.00"),
            minimo_por_pago=Decimal("0.00"),
        )
        self.client.force_login(self.admin)

    def test_los_seis_textos_de_la_descripcion(self):
        casos = [
            (
                dict(accion="primero"),
                "Cada lunes a las 08:00: mover «ejemplo.uno» a la posición 1.",
            ),
            (
                dict(disparador="mes", dia=31, hora=time(23, 0), accion="desactivar"),
                "Cada día 31 a las 23:00 (o el último día del mes si es más corto): "
                "desactivar «ejemplo.uno».",
            ),
            (
                dict(accion="posicion", posicion=3),
                "Cada lunes a las 08:00: mover «ejemplo.uno» a la posición 3 "
                "(o a la última si hay menos cuentas).",
            ),
            (
                dict(accion="medio"),
                "Cada lunes a las 08:00: mover «ejemplo.uno» al medio de la lista.",
            ),
            (
                dict(accion="ultimo"),
                "Cada lunes a las 08:00: mover «ejemplo.uno» al final de la lista.",
            ),
            (dict(accion="activar"), "Cada lunes a las 08:00: activar «ejemplo.uno»."),
        ]
        for i, (datos, esperado) in enumerate(casos):
            with self.subTest(esperado):
                r = self.regla(cuenta=self.uno, nombre=f"R{i}", **datos)
                # El día no cambia el texto; solo hace falta que no choque la unicidad.
                Regla.objects.filter(pk=r.pk).update(estado="inactiva")
                self.assertEqual(Regla.objects.get(pk=r.pk).descripcion, esperado)

    def test_sin_alias_usa_el_cbu(self):
        r = self.regla(cuenta=self.uno)
        r.cuenta.alias = None
        r.cuenta.cbu = "0000000000000000000001"
        self.assertIn("«Destinatario Ejemplo — CBU terminado en 0001»", r.descripcion)

    def seis(self):
        self.regla(cuenta=self.uno, nombre="Uno A", hora=time(8, 0))
        self.regla(cuenta=self.uno, nombre="Uno B", hora=time(9, 0), accion="desactivar")
        self.regla(cuenta=self.rodrigo, nombre="Rodrigo A", hora=time(8, 0))
        self.regla(cuenta=self.tincho, nombre="Tincho A", hora=time(8, 0))
        self.regla(cuenta=self.jimena, nombre="Jimena A", hora=time(8, 0), estado="inactiva")
        self.regla(cuenta=self.martin, nombre="Martin A", hora=time(8, 0), estado="inactiva")

    def nombres(self, **params):
        r = self.client.get(LISTA, params)
        self.assertEqual(r.status_code, 200)
        return sorted(x.nombre for x in r.context["reglas"])

    def test_filtro_estado(self):
        self.seis()
        self.assertEqual(len(self.nombres(estado="inactiva")), 2)

    def test_busqueda_por_alias(self):
        self.seis()
        self.assertEqual(self.nombres(q="ejemplo.uno"), ["Uno A", "Uno B"])
        self.assertEqual(self.nombres(q="destinatario ejemplo"), ["Uno A", "Uno B"])
        self.assertEqual(self.nombres(q="rodrigo a"), ["Rodrigo A"])

    def test_filtros_combinados(self):
        self.seis()
        self.assertEqual(self.nombres(accion="desactivar", estado="activa"), ["Uno B"])

    def test_valor_fuera_de_choices_no_filtra(self):
        self.seis()
        self.assertEqual(len(self.nombres(disparador="xyz")), 6)

    def test_operador_y_profesional_reciben_403(self):
        User = get_user_model()
        for rol in ("operator", "professional"):
            u = User.objects.create_user(f"u-{rol}", email=f"{rol}@example.com", role=rol)
            self.client.force_login(u)
            self.assertEqual(self.client.get(LISTA).status_code, 403, rol)
