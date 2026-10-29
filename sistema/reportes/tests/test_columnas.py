from sistema.cobros.models import ItemCobro
from sistema.reportes import servicios

from . import D, EN_OCT, DatosMartinO
from sistema.core.tests import SistemaTestCase


def _filas(cobros, fn):
    return list(fn(cobros, url_comprobante=lambda c: f"http://t/{c.pk}/" if c.comprobante else ""))


class ColumnasTest(DatosMartinO, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.cobros = servicios.Cobro.objects.all()

    def item(self, cobro, numero, profesional, monto, **kw):
        return ItemCobro.objects.create(
            cobro=cobro,
            numero=numero,
            id_prestacion=numero,
            nombre_profesional=profesional,
            nombre_prestacion=f"Prestación {numero}",
            nombre_tratamiento="Trat",
            monto=D(monto),
            **kw,
        )

    def test_cobro_pos_tiene_22_columnas_en_orden(self):
        c = self.cobro(
            self.mp,
            "10000.00",
            monto_neto=D("9500.00"),
            ultimos_4="1234",
            excedente=True,
            revisado=True,
            observaciones="obs",
            nombre_paciente="Ana",
        )
        self.item(c, 1, "Dra. X", "5000.00")
        self.item(c, 2, "Dr. Y", "5000.00")
        self.item(c, 3, "Dra. X", "0.01")
        fila = _filas(self.cobros, servicios.filas_por_cobro)[0]
        self.assertEqual(len(fila), len(servicios.TITULOS_COBRO))
        self.assertEqual(len(fila), 22)
        d = dict(zip(servicios.TITULOS_COBRO, fila, strict=True))
        self.assertEqual(d["Bruto"], D("10000.00"))
        self.assertEqual(d["Neto"], D("9500.00"))
        self.assertEqual(d["Profesionales"], "Dra. X, Dr. Y")
        self.assertEqual(d["Marcas"], "Excedente, Revisado")
        self.assertEqual(d["Cuenta"], "Martin MP")
        self.assertEqual(d["Estado"], "Finalizado")
        self.assertEqual(d["Método de cobro"], "Débito")
        self.assertEqual(d["Últimos 4 dígitos"], "1234")
        self.assertEqual(d["Fecha"], EN_OCT)
        self.assertTrue(d["Link al comprobante"].endswith(f"/{c.pk}/"))
        self.assertEqual(d["Observaciones"], "obs")

    def test_efectivo_deja_vacios_bruto_neto_terminal_y_link(self):
        cuenta = servicios.Cuenta.objects.create(
            destinatario=self.martin, tipo="efectivo", titular="Caja"
        )
        self.cobro(cuenta, "100.00", metodo="efectivo")
        d = dict(
            zip(
                servicios.TITULOS_COBRO,
                _filas(self.cobros, servicios.filas_por_cobro)[0],
                strict=True,
            )
        )
        self.assertIsNone(d["Bruto"])
        self.assertIsNone(d["Neto"])
        self.assertEqual(d["Terminal POS"], "")
        self.assertEqual(d["Link al comprobante"], "")
        self.assertEqual(d["Cuenta"], "Caja")

    def test_cancelado_con_otro_muestra_detalle(self):
        self.cobro(
            self.tincho,
            "100.00",
            estado="cancelado",
            motivo_cancelacion="otro",
            detalle_cancelacion="duplicado",
        )
        self.cobro(self.tincho, "100.00", estado="cancelado", motivo_cancelacion="no_pago")
        a, b = _filas(self.cobros, servicios.filas_por_cobro)
        i = servicios.TITULOS_COBRO.index("Motivo de cancelación")
        self.assertEqual(a[i], "Otro: duplicado")
        self.assertEqual(b[i], "El paciente no pagó")

    def test_por_item_repite_cobro_y_saldo_a_favor(self):
        c = self.cobro(self.tincho, "65000.00")
        self.item(c, 1, "Dra. X", "40000.00")
        self.item(c, 2, "Dr. Y", "25000.00", saldo_a_favor=True)
        a, b = _filas(self.cobros, servicios.filas_por_item)
        self.assertEqual(a[:22], b[:22])
        self.assertEqual(
            a[22:],
            [f"{c.pk}-1", "Trat", "Prestación 1", "Dra. X", D("40000.00"), "Pendiente de envío"],
        )
        self.assertEqual(b[24], "Saldo a favor")
        self.assertEqual(b[26], D("25000.00"))

    def test_cobro_sin_items_da_una_fila_con_seis_vacios(self):
        self.cobro(self.tincho, "100.00", faltan_items=True)
        filas = _filas(self.cobros, servicios.filas_por_item)
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0][22:], [None] * 6)
        self.assertEqual(len(filas[0]), len(servicios.TITULOS_ITEM))
