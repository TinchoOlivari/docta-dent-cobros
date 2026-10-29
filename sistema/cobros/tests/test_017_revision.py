"""Revisión 017: `?mes=` inválido o fuera de rango y lotes con excepción inesperada."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse

from sistema.cobros import servicios
from sistema.cobros.models import Cobro
from sistema.cobros.tests.test_017_operador import Base017
from sistema.destinatarios import servicios as dest_servicios
from sistema.destinatarios.models import Destinatario

MESES_MALOS = ("9999-12", "0000-01", "0001-01", "1999-12", "2101-01", "2026-13", "abc", "")


class MesInvalidoTest(Base017):
    def setUp(self):
        super().setUp()
        self.profesional = get_user_model().objects.create_user(
            "profrev", email="profrev@example.com", role="professional"
        )

    def urls(self):
        return [
            (self.admin, reverse("destinatarios:detalle", args=[self.dest.pk])),
            (self.admin, reverse("cobros:complementarios")),
            (self.admin, reverse("cobros:complementario_nuevo")),
            (self.admin, reverse("reportes:destinatario", args=[self.dest.pk])),
            (self.admin, reverse("arca:libro")),
            (self.profesional, reverse("usuarios:profesional")),
        ]

    def test_mes_fuera_de_rango_o_invalido_responde_200(self):
        for usuario, url in self.urls():
            self.client.force_login(usuario)
            for mes in MESES_MALOS:
                with self.subTest(url=url, mes=mes):
                    self.assertEqual(self.client.get(url, {"mes": mes}).status_code, 200)

    def test_limites_2000_y_2100_en_ficha_de_destinatario(self):
        self.client.force_login(self.admin)
        url = reverse("destinatarios:detalle", args=[self.dest.pk])
        for mes in ("2000-01", "2100-12"):
            self.assertEqual(self.client.get(url, {"mes": mes}).status_code, 200)
            self.assertEqual(self.client.get(url, {"mes": mes}).context["mes"].isoformat()[:7], mes)
        r = self.client.get(url, {"mes": "9999-12"})
        self.assertNotEqual(r.context["mes"].year, 9999)


class LoteInesperadoTest(Base017):
    def test_cancelar_cobros_sigue_tras_excepcion_inesperada_en_el_2do(self):
        a, b, c = self.cobro(), self.cobro(), self.cobro()
        original = servicios.cancelar_cobro

        def falla_en_b(cobro, **kw):
            if cobro.pk == b.pk:
                raise RuntimeError("boom")
            return original(cobro, **kw)

        with patch.object(servicios, "cancelar_cobro", side_effect=falla_en_b):
            with self.assertLogs(servicios.logger, "ERROR"):
                hechos = servicios.cancelar_cobros(
                    [a.pk, b.pk, c.pk], usuario=self.admin, motivo="no_pago"
                )
        self.assertEqual(hechos, ([a.pk, c.pk], [b.pk]))
        self.assertEqual(
            list(Cobro.objects.order_by("pk").values_list("estado", flat=True)),
            ["cancelado", "pendiente", "cancelado"],
        )

    def test_cambiar_estado_destinatarios_sigue_tras_excepcion_inesperada_en_el_2do(self):
        extra = [
            Destinatario.objects.create(nombre=f"Extra {i}", tipo="profesional") for i in (1, 2)
        ]
        ds = sorted([self.dest, *extra], key=lambda d: d.pk)
        original = dest_servicios.cambiar_estado_destinatario

        def falla_en_2(d, estado, **kw):
            if d.pk == ds[1].pk:
                raise RuntimeError("boom")
            return original(d, estado, **kw)

        with patch.object(dest_servicios, "cambiar_estado_destinatario", side_effect=falla_en_2):
            with self.assertLogs(dest_servicios.logger, "ERROR"):
                cambiados, omitidos = dest_servicios.cambiar_estado_destinatarios(
                    [d.pk for d in ds], "inactivo", usuario=self.admin
                )
        self.assertEqual((cambiados, omitidos), ([ds[0].pk, ds[2].pk], [ds[1].pk]))
