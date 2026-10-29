"""021 B: lista de cobros (revisar en lote, cancelar en lote, unaccent, método, filtros)."""

from datetime import datetime
from decimal import Decimal

from django.urls import reverse

from sistema.arca.models import ComprobanteFiscal
from sistema.arca.tests.fabricas import cobro_facturable, emisor_prueba, factura_prueba
from sistema.cobros.models import Cobro
from sistema.cobros.tests.test_018_lista import Base018Lista
from sistema.core.tiempo import ARG
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal


def _nbsp(t):
    return t.replace("\xa0", " ")


class RevisarLoteTest(Base018Lista):
    def test_20_elegidos_con_un_excedente_sin_comentario_revisa_19_y_avisa(self):
        cobros = [self.cobro() for _ in range(20)]
        exc = cobros[7]
        Cobro.objects.filter(pk=exc.pk).update(excedente=True)
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("cobros:revisado_lote"),
            {"pk": [c.pk for c in cobros], "next": reverse("cobros:lista")},
            follow=True,
        )
        self.assertEqual(Cobro.objects.filter(revisado=True).count(), 19)
        self.assertFalse(Cobro.objects.get(pk=exc.pk).revisado)
        self.assertContains(r, "Revisados: 19.")
        self.assertContains(r, f"1 cobro con excedente necesita comentario: N.º {exc.pk}.")

    def test_varios_excedentes_sin_comentario_van_en_plural(self):
        a, b = self.cobro(), self.cobro()
        Cobro.objects.filter(pk__in=[a.pk, b.pk]).update(excedente=True)
        self.client.force_login(self.admin)
        r = self.client.post(reverse("cobros:revisado_lote"), {"pk": [a.pk, b.pk]}, follow=True)
        self.assertContains(r, "Revisados: 0.")
        self.assertContains(
            r, f"2 cobros con excedente necesitan comentario: N.º {a.pk}, N.º {b.pk}."
        )

    def test_sin_elegidos_un_solo_aviso(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("cobros:revisado_lote"), {}, follow=True)
        self.assertContains(r, "Elegí al menos un cobro.", count=1)

    def test_el_operador_no_puede(self):
        c = self.cobro()
        self.client.force_login(self.ana)
        r = self.client.post(reverse("cobros:revisado_lote"), {"pk": [c.pk]})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Cobro.objects.get(pk=c.pk).revisado)

    def test_no_acepta_get(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("cobros:revisado_lote")).status_code, 405)


class BusquedaSinAcentosTest(Base018Lista):
    def _casos(self, escritos, nombre):
        self.cobro(nombre_paciente=nombre)
        self.cobro(nombre_paciente="Otro Paciente")
        for usuario in (self.admin, self.ana):
            for q in escritos:
                with self.subTest(rol=usuario.role, q=q):
                    self.assertEqual(len(self.pks(usuario, q=q)), 1)

    def test_ibanez(self):
        self._casos(["ibanez", "IBAÑEZ", "Ibáñez"], "María Ibáñez")

    def test_munoz(self):
        self._casos(["munoz", "MUÑOZ", "Muñoz"], "José Muñoz")

    def test_alias_y_destinatario_del_administrador(self):
        dest = crear_destinatario(usuario=None, nombre="Núñez Ejemplo", tipo="clinica")
        cuenta = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest,
            alias="a.b",
            tope_mensual=D("60000.00"),
        )
        self.cobro(cuenta=cuenta, destinatario=dest)
        self.cobro()
        self.assertEqual(len(self.pks(q="nunez")), 1)


class FiltrosTest(Base018Lista):
    def test_hoy_incluye_las_00_00_y_excluye_el_dia_anterior_a_las_23_59_59(self):
        dentro = self.cobro(fecha=datetime(2026, 10, 28, 0, 0, tzinfo=ARG))
        self.cobro(fecha=datetime(2026, 10, 27, 23, 59, 59, tzinfo=ARG))
        self.assertEqual(self.pks(desde="2026-10-28", hasta="2026-10-28"), [dentro.pk])

    def test_factura_con_error_incluye_el_reintento_desde_el_primer_fallo(self):
        emisor = emisor_prueba()
        sin_fallo, un_fallo = cobro_facturable(), cobro_facturable()
        factura_prueba(sin_fallo, emisor=emisor, estado="pendiente", intentos=0)
        factura_prueba(un_fallo, emisor=emisor, estado="pendiente", intentos=1)
        pks = self.pks(factura_con_error="on")
        self.assertIn(un_fallo.pk, pks)
        self.assertNotIn(sin_fallo.pk, pks)
        self.assertEqual(ComprobanteFiscal.objects.filter(intentos=1).count(), 1)
