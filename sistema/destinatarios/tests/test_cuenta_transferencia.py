from decimal import Decimal

from django.core.exceptions import ValidationError

from sistema.core.tests import SistemaTestCase
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.destinatarios.servicios import crear_cuenta, crear_destinatario

D = Decimal
CBU = "0000000000000000000001"


class CuentaTransferenciaTest(SistemaTestCase):
    def setUp(self):
        self.d = crear_destinatario(usuario=None, nombre="Martin Test", tipo="profesional")

    def alta(self, **kw):
        datos = dict(
            alias="alias.uno",
            cbu=CBU,
            tope_mensual=D("500000.00"),
            tope_semanal=D("150000.00"),
            minimo_por_pago=D("10000.00"),
        )
        datos.update(kw)
        return crear_cuenta(usuario=None, tipo="transferencia", destinatario=self.d, **datos)

    def rechaza(self, mensaje, **kw):
        with self.assertRaises(ValidationError) as cm:
            self.alta(**kw)
        self.assertIn(mensaje, cm.exception.messages)

    def test_us1_1_alta_completa_queda_activa_con_fecha(self):
        c = self.alta()
        self.assertEqual(c.estado, "activa")
        self.assertIsNotNone(c.fecha_activacion)
        self.assertEqual(c.tope_mensual, D("500000.00"))
        self.assertIn(c, Cuenta.objects.habilitadas())

    def test_minimo_igual_al_tope_ok_uno_arriba_rechazado(self):
        self.alta(minimo_por_pago=D("500000.00"), tope_semanal=D("0.00"))
        self.rechaza(
            "El mínimo por pago no puede superar el tope mensual.",
            alias="otro.alias",
            cbu=None,
            minimo_por_pago=D("500000.01"),
            tope_semanal=D("0.00"),
        )

    def test_minimo_cero_ok(self):
        self.assertEqual(self.alta(minimo_por_pago=D("0.00")).minimo_por_pago, D("0.00"))

    def test_semanal_uno_abajo_del_minimo_rechazado_justo_ok(self):
        self.rechaza(
            "El tope semanal no puede ser menor que el mínimo por pago.",
            tope_semanal=D("9999.99"),
        )
        self.assertEqual(self.alta(tope_semanal=D("10000.00")).tope_semanal, D("10000.00"))

    def test_semanal_cero_es_sin_tope_ok(self):
        self.assertEqual(self.alta(tope_semanal=D("0.00")).tope_semanal, D("0.00"))

    def test_semanal_igual_al_mensual_ok_uno_arriba_rechazado(self):
        self.alta(tope_semanal=D("500000.00"))
        self.rechaza(
            "El tope semanal no puede superar el tope mensual.",
            alias="otro.alias",
            cbu=None,
            tope_semanal=D("500000.01"),
        )

    def test_semanal_distinto_de_cero_con_minimo_cero_ok(self):
        c = self.alta(minimo_por_pago=D("0.00"), tope_semanal=D("0.01"))
        self.assertEqual(c.tope_semanal, D("0.01"))

    def test_tope_mensual_cero_rechazado_un_centavo_ok(self):
        self.rechaza(
            "El tope mensual debe ser mayor que $0,00.",
            tope_mensual=D("0.00"),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )
        c = self.alta(tope_mensual=D("0.01"), tope_semanal=D("0.00"), minimo_por_pago=D("0.00"))
        self.assertEqual(c.tope_mensual, D("0.01"))

    def test_tope_mensual_bajado_a_9999_99_con_minimo_10000_rechazado(self):
        self.rechaza(
            "El mínimo por pago no puede superar el tope mensual.",
            tope_mensual=D("9999.99"),
            tope_semanal=D("0.00"),
        )

    def test_semanal_y_minimo_ausentes_quedan_en_cero(self):
        c = self.alta(tope_semanal=None, minimo_por_pago=None)
        self.assertEqual((c.tope_semanal, c.minimo_por_pago), (D("0.00"), D("0.00")))

    def test_sin_alias_ni_cbu_rechazado_solo_uno_ok(self):
        self.rechaza("Cargá un alias, un CBU o CVU, o los dos.", alias="", cbu="")
        self.assertIsNone(self.alta(cbu="").cbu)
        self.assertIsNone(self.alta(alias="", cbu="0" * 21 + "2").alias)

    def test_cbu_21_y_23_digitos_y_con_letra_rechazado_22_ok(self):
        for cbu in ("0" * 21, "0" * 21 + "a"):
            self.rechaza("El CBU o CVU debe tener 22 dígitos, solo números.", cbu=cbu)
        with self.assertRaises(ValidationError):  # 23: lo frena el largo máximo de la columna
            self.alta(cbu="0" * 23)
        self.assertEqual(self.alta().cbu, CBU)

    def test_alias_de_cuenta_archivada_repetido_rechazado(self):
        c = self.alta()
        Cuenta.objects.filter(pk=c.pk).update(estado="archivada", posicion=None)
        self.rechaza("Ya existe una cuenta con ese alias.", cbu=None)

    def test_alias_que_difiere_en_mayusculas_rechazado(self):
        self.alta()
        self.rechaza("Ya existe una cuenta con ese alias.", alias="Alias.Uno", cbu=None)

    def test_cbu_repetido_rechazado(self):
        self.alta()
        self.rechaza("Ya existe una cuenta con ese CBU o CVU.", alias="otro.alias")

    def test_titular_distinto_del_destinatario_ok(self):
        self.assertEqual(self.alta(titular="Jimena Test").titular, "Jimena Test")

    def test_monto_con_3_decimales_rechazado(self):
        with self.assertRaises(ValidationError):
            self.alta(tope_mensual=D("500000.001"))

    def test_monto_negativo_rechazado(self):
        with self.assertRaises(ValidationError):
            self.alta(minimo_por_pago=D("-0.01"))

    def test_fr010_varias_cuentas_de_distinto_tipo_habilitadas(self):
        otro = Destinatario.objects.get(pk=self.d.pk)
        t1 = self.alta()
        t2 = self.alta(alias="segunda.cuenta", cbu=None)
        ef = crear_cuenta(usuario=None, tipo="efectivo", destinatario=otro)
        self.assertEqual(set(Cuenta.objects.habilitadas()), {t1, t2, ef})

    def test_fr010_varias_cuentas_de_cada_tipo_habilitadas(self):
        from sistema.destinatarios.models import Proveedor

        ids = {
            self.alta().pk,
            self.alta(alias="otra.cuenta", cbu=None).pk,
            crear_cuenta(usuario=None, tipo="efectivo", destinatario=self.d).pk,
            crear_cuenta(
                usuario=None,
                tipo="terminal_pos",
                destinatario=self.d,
                proveedor=Proveedor.objects.get(nombre="Mercadopago"),
                objetivo=D("1.00"),
            ).pk,
        }
        self.assertEqual(set(Cuenta.objects.habilitadas().values_list("pk", flat=True)), ids)
