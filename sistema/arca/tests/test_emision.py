from datetime import date
from decimal import Decimal
from unittest import mock

from sistema.arca import servicios as arca
from sistema.arca.models import ComprobanteFiscal
from sistema.arca.tests.fabricas import (
    AHORA,
    CAE,
    ArcaFalso,
    cobro_facturable,
    emisor_prueba,
    factura_prueba,
)
from sistema.auditoria.models import Evento
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase


class EmisionTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        reloj = mock.patch("django.utils.timezone.now", return_value=AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)
        self.arca = ArcaFalso(self)
        self.emisor = emisor_prueba()

    def emitir(self, monto="65000.00", dni="30111222", emisor=None, **campos):
        cobro = cobro_facturable(monto=monto, dni_paciente=dni, nombre_paciente="Paciente Prueba")
        factura = factura_prueba(cobro, emisor=emisor or self.emisor, **campos)
        self.assertEqual(arca.emitir(factura.pk), "emitido")
        return ComprobanteFiscal.objects.get(pk=factura.pk)

    def pedido(self):
        return self.arca.solicitar_cae.call_args.args[2]

    def test_efectivo_queda_emitida_con_numero_cae_e_importes(self):
        f = self.emitir()
        self.assertEqual((f.numero, f.cae, f.cae_vence), (42, CAE, date(2026, 10, 30)))
        self.assertEqual(
            (f.neto, f.iva, f.total),
            (Decimal("53719.01"), Decimal("11280.99"), Decimal("65000.00")),
        )
        self.assertEqual((f.fecha, f.cbte_tipo, f.punto_venta), (date(2026, 10, 20), 6, 3))
        self.assertIsNone(f.numero_intentado)
        self.assertIsNone(f.en_curso_desde)
        self.assertEqual(f.intentos, 1)

    def test_el_pedido_lleva_documento_y_montos_decimal(self):
        self.emitir()
        pedido = self.pedido()
        self.assertEqual((pedido["doc_tipo"], pedido["doc_numero"]), (96, "30111222"))
        self.assertEqual((pedido["numero"], pedido["concepto"], pedido["alicuota_id"]), (42, 2, 5))
        for campo in ("neto", "iva", "total"):
            self.assertIsInstance(pedido[campo], Decimal)
        self.assertEqual(pedido["servicio_desde"], date(2026, 10, 20))
        self.assertEqual(pedido["vto_pago"], date(2026, 10, 20))

    def test_dni_con_puntos_se_manda_solo_con_digitos(self):
        f = self.emitir(dni="30.111.222")
        self.assertEqual((f.doc_tipo, f.doc_numero), (96, "30111222"))

    def test_sin_dni_va_como_consumidor_final(self):
        f = self.emitir(dni="")
        self.assertEqual((f.doc_tipo, f.doc_numero), (99, "0"))
        self.assertEqual(self.pedido()["doc_tipo"], 99)

    def test_dni_de_solo_ceros_va_como_consumidor_final(self):
        self.assertEqual(self.emitir(dni="000").doc_tipo, 99)

    def test_documento_cargado_en_la_factura_gana_sobre_el_dni_del_cobro(self):
        f = self.emitir(doc_tipo=80, doc_numero="20000000001", receptor_nombre="Empresa Prueba")
        self.assertEqual(
            (f.doc_tipo, f.doc_numero, f.receptor_nombre), (80, "20000000001", "Empresa Prueba")
        )

    def test_servicio_cargado_por_el_administrador_gana_sobre_la_fecha_del_cobro(self):
        self.emitir(servicio_desde=date(2026, 10, 1), servicio_hasta=date(2026, 10, 15))
        self.assertEqual(self.pedido()["servicio_desde"], date(2026, 10, 1))
        self.assertEqual(self.pedido()["servicio_hasta"], date(2026, 10, 15))

    def test_terminal_pos_factura_el_bruto(self):
        cuenta = cobro_facturable().cuenta  # el modelo no liga el tipo de cuenta al método
        cobro = cobro_prueba(
            cuenta=cuenta,
            monto="30000.00",
            monto_neto="29100.50",
            metodo="debito",
            estado="finalizado",
        )
        f = factura_prueba(cobro, emisor=self.emisor)
        arca.emitir(f.pk)
        f.refresh_from_db()
        self.assertEqual(f.total, Decimal("30000.00"))
        self.assertEqual(f.neto + f.iva, Decimal("30000.00"))

    def test_importes_en_los_limites_del_redondeo(self):
        casos = {  # total: (neto, iva) con alícuota 21 %, ROUND_HALF_UP
            "0.01": ("0.01", "0.00"),
            "0.02": ("0.02", "0.00"),
            "1.00": ("0.83", "0.17"),
            "1.21": ("1.00", "0.21"),
            "1.22": ("1.01", "0.21"),
            "121.00": ("100.00", "21.00"),
        }
        for total, (neto, iva) in casos.items():
            with self.subTest(total=total):
                f = factura_prueba(cobro_facturable(monto=total), emisor=self.emisor)
                f.cobro.refresh_from_db()
                self.assertEqual(
                    arca.importes(f, self.emisor), (Decimal(neto), Decimal(iva), Decimal(total))
                )

    def test_importes_con_cada_alicuota(self):
        casos = {"0": "100.00", "10.5": "90.50", "27": "78.74"}
        for n, (alicuota, neto) in enumerate(casos.items()):
            with self.subTest(alicuota=alicuota):
                emisor = emisor_prueba(cuit=f"2000000010{n}", alicuota_iva=alicuota)
                f = factura_prueba(cobro_facturable(monto="100.00"), emisor=emisor)
                f.cobro.refresh_from_db()
                neto_calculado, iva, total = arca.importes(f, emisor)
                self.assertEqual((neto_calculado, neto_calculado + iva), (Decimal(neto), total))

    def test_factura_c_no_discrimina_iva(self):
        emisor = emisor_prueba(
            cuit="20000000002", tipo_comprobante="c", condicion_iva="monotributo"
        )
        f = self.emitir(emisor=emisor)
        self.assertEqual(
            (f.neto, f.iva, f.total), (Decimal("65000.00"), Decimal("0.00"), Decimal("65000.00"))
        )
        self.assertEqual(f.cbte_tipo, 11)
        self.assertIsNone(self.pedido()["alicuota_id"])

    def test_saldo_a_favor_no_cambia_el_total_del_cobro(self):
        f = self.emitir(monto="40000.00")
        self.assertEqual(f.total, Decimal("40000.00"))

    def test_nota_de_credito_repite_importes_y_asocia_la_factura(self):
        factura = self.emitir()
        nota = ComprobanteFiscal.objects.create(
            cobro=factura.cobro,
            emisor=self.emisor,
            tipo="nota_credito",
            asociado=factura,
            vigente=False,
            concepto="servicios",
            descripcion="Servicios odontológicos",
        )
        self.assertEqual(arca.emitir(nota.pk), "emitido")
        nota.refresh_from_db()
        self.assertEqual((nota.cbte_tipo, nota.numero), (8, 43))
        self.assertEqual(
            (nota.neto, nota.iva, nota.total), (factura.neto, factura.iva, factura.total)
        )
        asociado = self.pedido()["asociado"]
        self.assertEqual(
            (asociado["tipo"], asociado["numero"], asociado["cuit"]), (6, 42, "20000000001")
        )

    def test_emitir_deja_un_evento_con_exito(self):
        f = self.emitir()
        evento = Evento.objects.get(tipo=Evento.Tipo.EMISION_ARCA, entidad_id=f.pk)
        self.assertTrue(evento.exito)
        self.assertIsNone(evento.usuario)
        self.assertIn("Intento 1: ok", evento.detalle)

    def test_emitido_no_se_emite_otra_vez(self):
        f = self.emitir()
        self.assertEqual(arca.emitir(f.pk), "omitido")
        self.arca.solicitar_cae.assert_called_once()
