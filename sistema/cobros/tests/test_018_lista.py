"""018 B1: lista de cobros (filtrado manual, combobox, exportar, columnas, monto corto, orden)."""

from decimal import Decimal

from django.urls import reverse

from sistema.cobros.tests.test_017_operador import Base017

LISTA = reverse("cobros:lista")
HTMX = {"HTTP_HX_REQUEST": "true"}


def _nbsp(texto):
    return texto.replace("\xa0", " ")


class Base018Lista(Base017):
    def get(self, usuario=None, htmx=False, **params):
        self.client.force_login(usuario or self.admin)
        return self.client.get(LISTA, params, **(HTMX if htmx else {}))

    def html(self, usuario=None, **params):
        return _nbsp(self.get(usuario, **params).content.decode())

    def pks(self, usuario=None, **params):
        return [c.pk for c in self.get(usuario, **params).context["pagina"]]


class LimpiarFiltrosTest(Base018Lista):
    def setUp(self):
        super().setUp()
        self.cobro()

    def n_limpiar(self, **params):
        return self.html(**params).count("Limpiar filtros")

    def test_sin_parametros_no_hay_enlace(self):
        self.assertEqual(self.n_limpiar(), 0)

    def test_orden_y_pagina_no_cuentan_como_filtro(self):
        self.assertEqual(self.n_limpiar(orden="-monto"), 0)
        self.assertEqual(self.n_limpiar(page=1), 0)

    def test_q_vacio_o_en_blanco_no_cuenta(self):
        self.assertEqual(self.n_limpiar(q=""), 0)
        self.assertEqual(self.n_limpiar(q="  "), 0)

    def test_un_filtro_con_valor_muestra_un_enlace(self):
        self.assertEqual(self.n_limpiar(estado="pendiente"), 1)

    def test_con_la_tabla_vacia_se_suma_el_del_estado_vacio(self):
        self.assertEqual(self.n_limpiar(estado="cancelado"), 2)

    def test_enlace_viejo_con_cuenta_filtra_y_muestra_el_enlace(self):
        self.assertEqual(self.n_limpiar(cuenta=self.cuenta.pk), 1)


class FiltroCuentaYComboTest(Base018Lista):
    def test_no_se_dibuja_el_campo_cuenta_y_el_enlace_viejo_filtra(self):
        a = self.cobro()
        otra = self.cuenta_otra()
        b = self.cobro(cuenta=otra)
        self.assertEqual(self.pks(cuenta=self.cuenta.pk), [a.pk])
        self.assertEqual(self.pks(cuenta=otra.pk), [b.pk])

    def cuenta_otra(self):
        from sistema.destinatarios.servicios import crear_cuenta

        return crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=self.dest,
            alias="prueba.dos",
            tope_mensual=Decimal("60000.00"),
        )


class ColumnasTest(Base018Lista):
    def test_el_subtotal_sigue_con_pesos_completos(self):
        self.cobro(monto=Decimal("150000.00"))
        self.assertIn("Subtotal sin cancelados: $150.000,00", self.html())


class ExportarTest(Base018Lista):
    def test_exportar_es_solo_del_administrador(self):
        self.cobro()
        for usuario in (self.ana,):
            self.assertNotContains(self.get(usuario), 'id="exportar"')
            self.assertNotContains(self.get(usuario, htmx=True), 'id="exportar"')


class OrdenTest(Base018Lista):
    def test_destinatario_ordena_por_nombre(self):
        from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

        dest_b = crear_destinatario(usuario=None, nombre="B Destino", tipo="profesional")
        dest_a = crear_destinatario(usuario=None, nombre="A Destino", tipo="profesional")
        cuenta_b = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest_b,
            alias="b.alias",
            tope_mensual=Decimal("60000.00"),
        )
        cuenta_a = crear_cuenta(
            usuario=None,
            tipo="transferencia",
            destinatario=dest_a,
            alias="a.alias",
            tope_mensual=Decimal("60000.00"),
        )
        b = self.cobro(destinatario=dest_b, cuenta=cuenta_b)
        a = self.cobro(destinatario=dest_a, cuenta=cuenta_a)
        self.assertEqual(self.pks(orden="destinatario"), [a.pk, b.pk])
        self.assertEqual(self.pks(orden="-destinatario"), [b.pk, a.pk])

    def test_revisado_ordena_sin_revisar_primero(self):
        uno = self.cobro()
        dos = self.cobro(revisado=True)
        self.assertEqual(self.pks(orden="revisado"), [uno.pk, dos.pk])
        self.assertEqual(self.pks(orden="-revisado"), [dos.pk, uno.pk])

    def test_sin_orden_el_mas_nuevo_primero_y_desempata_por_pk(self):
        uno = self.cobro()
        dos = self.cobro()
        self.assertEqual(self.pks(), [dos.pk, uno.pk])

    def test_numero_del_operador_cae_al_orden_por_defecto(self):
        uno = self.cobro()
        dos = self.cobro()
        self.assertEqual(self.pks(self.ana, orden="numero"), [dos.pk, uno.pk])
        self.assertEqual(self.pks(self.ana, orden="-numero"), [dos.pk, uno.pk])
        self.assertEqual(self.get(self.ana, orden="numero").context["orden"], "")

    def test_numero_del_administrador_se_acepta_sin_encabezado(self):
        uno = self.cobro()
        dos = self.cobro()
        self.assertEqual(self.pks(orden="numero"), [uno.pk, dos.pk])

    def test_valor_fuera_de_la_lista_blanca_usa_el_orden_por_defecto(self):
        self.cobro()
        self.assertEqual(self.get(orden="inexistente").status_code, 200)


class CsvYExcelTest(Base018Lista):
    def test_la_marca_del_csv_dice_sin_vincular_a_dentalink(self):
        self.cobro(faltan_items=True)
        self.client.force_login(self.admin)
        r = self.client.get(
            reverse("reportes:cobros_descargar"), {"formato": "csv", "por": "cobro"}
        )
        texto = b"".join(r.streaming_content).decode("utf-8-sig")
        self.assertIn("Sin vincular a Dentalink", texto)
        self.assertNotIn("Faltan ítems", texto)
