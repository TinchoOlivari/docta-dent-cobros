from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError

from sistema.cobros import servicios
from sistema.cobros.models import ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.tests import SistemaTestCase
from sistema.dentalink.models import ConfiguracionDentalink
from sistema.dentalink.tests.test_lectura import DR_Y, prestacion, tratamiento
from sistema.destinatarios.models import Cuenta, Destinatario

D = Decimal
DRA_Z = {"id": 13, "nombre": "Dr. Z"}
TRATAMIENTOS = "sistema.dentalink.cliente.tratamientos"


def item_dict(id_prestacion=1, monto="1000.00", **kw):
    return {"id_prestacion": id_prestacion, "monto": D(monto), "id_profesional": None, **kw}


class DatosCobro(SistemaTestCase):
    def setUp(self):
        dest = Destinatario.objects.create(nombre="Clínica", tipo="clinica", cuit="20000000001")
        self.cuenta = Cuenta.objects.create(
            destinatario=dest,
            tipo="transferencia",
            alias="alias.uno",
            tope_mensual=D("1000000.00"),
            tope_semanal=D("0.00"),
            minimo_por_pago=D("0.00"),
        )

    def item(self, estado_envio="pendiente", cobro_estado="pendiente", monto="40000.00", pk_pres=1):
        cobro = cobro_prueba(cuenta=self.cuenta, monto=D(monto), estado=cobro_estado)
        return ItemCobro.objects.create(
            cobro=cobro,
            numero=1,
            id_prestacion=pk_pres,
            monto=D(monto),
            estado_envio=estado_envio,
        )

    def disponible(self, cobro=None, id_prestacion=1):
        with mock.patch(TRATAMIENTOS, return_value=[tratamiento([prestacion(1, "40000", "0")])]):
            lista = servicios.prestaciones_para_cobrar(501, cobro=cobro)
        for t in lista:
            for p in t["prestaciones"]:
                if p["id"] == id_prestacion:
                    return p["saldo_disponible"]
        return None


class SaldoTest(DatosCobro):
    def test_sin_items_disponible_es_el_saldo(self):
        self.assertEqual(self.disponible(), D("40000.00"))

    def test_item_pendiente_agota_la_prestacion(self):
        self.item()
        self.assertIsNone(self.disponible())

    def test_cobro_cancelado_libera_la_prestacion(self):
        i = self.item(cobro_estado="cancelado")
        self.assertEqual(self.disponible(), D("40000.00"))
        self.assertEqual(servicios.reservado(i.id_prestacion), D("0.00"))

    def test_error_y_revisar_restan(self):
        for n, estado in enumerate(("error", "revisar"), start=10):
            with self.subTest(estado):
                self.item(estado, monto="15000.00", pk_pres=n)
                self.assertEqual(servicios.reservado(n), D("15000.00"))

    def test_otros_estados_no_restan(self):
        estados = ("requiere_ajuste", "enviado", "cancelado", "ajustado", "historico")
        for n, estado in enumerate(estados, start=20):
            with self.subTest(estado):
                self.item(estado, monto="15000.00", pk_pres=n)
                self.assertEqual(servicios.reservado(n), D("0.00"))

    def test_reservado_excluye_el_item_indicado(self):
        i = self.item(monto="15000.00")
        self.assertEqual(servicios.reservado(1), D("15000.00"))
        self.assertEqual(servicios.reservado(1, excluir_item=i.pk), D("0.00"))

    def test_edicion_el_item_propio_no_resta(self):
        i = self.item(monto="15000.00")
        self.assertEqual(self.disponible(), D("25000.00"))
        self.assertEqual(self.disponible(cobro=i.cobro), D("40000.00"))

    def test_edicion_item_enviado_suma_su_monto(self):
        i = self.item("enviado", monto="15000.00")
        with mock.patch(
            TRATAMIENTOS, return_value=[tratamiento([prestacion(1, "40000", "15000")])]
        ):
            lista = servicios.prestaciones_para_cobrar(501, cobro=i.cobro)
        self.assertEqual(lista[0]["prestaciones"][0]["saldo_disponible"], D("40000.00"))

    def test_lee_lo_reservado_antes_de_dentalink(self):
        orden = []
        original = servicios._reservan

        def reservan():
            orden.append("reservado")
            return original()

        def tratamientos(_):
            orden.append("dentalink")
            return []

        with (
            mock.patch.object(servicios, "_reservan", reservan),
            mock.patch(TRATAMIENTOS, side_effect=tratamientos),
        ):
            servicios.prestaciones_para_cobrar(501)
        self.assertEqual(orden, ["reservado", "dentalink"])

    def test_saldo_a_favor_aparece_con_saldo_cero(self):
        ConfiguracionDentalink.objects.create(
            pk=1, id_prestacion_saldo_a_favor=9100, nombre_prestacion_saldo_a_favor="Saldo a favor"
        )
        with mock.patch(
            TRATAMIENTOS,
            return_value=[tratamiento([prestacion(9050, "0.00", "0.00", id_prestacion=9100)])],
        ):
            p = servicios.prestaciones_para_cobrar(501)[0]["prestaciones"][0]
        self.assertTrue(p["saldo_a_favor"])


class ValidarItemsTest(DatosCobro):
    def setUp(self):
        super().setUp()
        ConfiguracionDentalink.objects.create(
            pk=1, id_prestacion_saldo_a_favor=9100, nombre_prestacion_saldo_a_favor="Saldo a favor"
        )

    def validar(self, items, extra=()):
        trat = [
            tratamiento(
                [
                    prestacion(1, "40000", "0", id_profesional=12),
                    prestacion(2, "10000", "0"),
                    prestacion(9050, "0.00", "0.00", id_prestacion=9100),
                    *extra,
                ],
                profesionales=(DR_Y, DRA_Z),
            )
        ]
        # Dr. Z fuera del tratamiento: se saca del dict
        trat[0]["profesionales"] = [DR_Y]
        with mock.patch(TRATAMIENTOS, return_value=trat):
            prestaciones = servicios.prestaciones_para_cobrar(501)
        return servicios._validar_items(items, prestaciones)

    def assertRechaza(self, items, mensaje):
        with self.assertRaisesMessage(ValidationError, mensaje):
            self.validar(items)

    def test_monto_dentro_del_saldo_se_acepta(self):
        self.assertEqual(self.validar([item_dict(monto="15000.00")])[0].monto, D("15000.00"))

    def test_monto_igual_al_saldo_se_acepta(self):
        self.validar([item_dict(monto="40000.00")])

    def test_un_centavo_sobre_el_saldo_se_rechaza(self):
        self.assertRechaza(
            [item_dict(monto="40000.01")],
            "supera el saldo disponible de $40.000,00",
        )

    def test_monto_cero_se_rechaza(self):
        self.assertRechaza([item_dict(monto="0.00")], "El monto del ítem debe ser mayor que $0,00.")

    def test_un_centavo_se_acepta(self):
        self.validar([item_dict(monto="0.01")])

    def test_sin_items_se_rechaza(self):
        self.assertRechaza([], "Elegí al menos una prestación.")

    def test_prestacion_repetida(self):
        self.assertRechaza([item_dict(), item_dict()], "Elegiste 2 veces la misma prestación.")

    def test_prestacion_fuera_de_la_lista(self):
        self.assertRechaza(
            [item_dict(id_prestacion=99)], "La prestación no tiene saldo para cobrar."
        )

    def test_profesional_del_tratamiento_se_acepta_y_otro_se_rechaza(self):
        self.assertEqual(self.validar([item_dict(id_profesional=12)])[0].id_profesional, 12)
        self.assertRechaza(
            [item_dict(id_profesional=13)], "El profesional no está en ese tratamiento."
        )

    def test_sin_profesional_usa_el_propuesto(self):
        i = self.validar([item_dict()])[0]
        self.assertEqual((i.id_profesional, i.nombre_profesional), (12, "Dr. Y"))

    def test_copia_los_nombres_de_dentalink(self):
        i = self.validar([item_dict(id_prestacion=2)])[0]
        self.assertEqual(
            (i.id_tratamiento, i.nombre_tratamiento, i.nombre_prestacion, i.numero),
            (1, "Ortodoncia", "Prestación 2", 1),
        )

    def test_saldo_a_favor_sin_limite_y_con_profesional_propuesto(self):
        for monto in ("5000.00", "0.01"):
            items = [
                item_dict(monto="35000.00"),
                {
                    "id_prestacion": None,
                    "monto": D(monto),
                    "id_profesional": 13,
                    "saldo_a_favor": True,
                },
            ]
            i = self.validar(items)[1]
            self.assertTrue(i.saldo_a_favor)
            self.assertEqual(i.id_profesional, 12)

    def test_dos_saldos_a_favor_se_rechazan(self):
        sf = {
            "id_prestacion": None,
            "monto": D("1.00"),
            "id_profesional": None,
            "saldo_a_favor": True,
        }
        self.assertRechaza([sf, dict(sf)], "Solo se admite un ítem de saldo a favor.")

    def test_saldo_a_favor_sin_la_prestacion_en_dentalink(self):
        ConfiguracionDentalink.objects.update(
            id_prestacion_saldo_a_favor=None, nombre_prestacion_saldo_a_favor=""
        )
        sf = {
            "id_prestacion": None,
            "monto": D("1.00"),
            "id_profesional": None,
            "saldo_a_favor": True,
        }
        self.assertRechaza([sf], "El paciente no tiene la prestación «saldo a favor»")

    def test_varios_tratamientos_del_mismo_paciente(self):
        t1 = tratamiento([prestacion(1, "40000", "0")])
        t2 = {**tratamiento([prestacion(5, "8000", "0")]), "id": 2, "nombre": "Implantes"}
        with mock.patch(TRATAMIENTOS, return_value=[t1, t2]):
            prestaciones = servicios.prestaciones_para_cobrar(501)
        items = servicios._validar_items([item_dict(1), item_dict(5, "8000.00")], prestaciones)
        self.assertEqual([i.id_tratamiento for i in items], [1, 2])
