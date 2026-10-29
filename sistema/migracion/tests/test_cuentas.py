from datetime import datetime
from decimal import Decimal as D

from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta, Destinatario, TopeVigente
from sistema.destinatarios.servicios import tope_de
from sistema.migracion import servicios
from sistema.migracion.tests import fabricas as f


class CuentasBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        f.usuario("completar", role="professional")
        self.n = 0

    def fila(self, name, **kw):
        self.n += 1
        kw.setdefault("alias", f"alias.prueba.{self.n}")
        return f.fila(name, **kw)

    def migrar(self, tipos=None):
        datos = {"para_completar_pagos": "completar", "tipos": tipos or {}, "vinculos": {}}
        servicios.migrar(datos, definitiva=True)

    def cuenta(self, alias):
        return Cuenta.objects.get(alias=alias)


class TopesTest(CuentasBase):
    def test_topes_pasan_como_decimales_con_dos_decimales(self):
        self.fila(
            "Tincho",
            alias="alias.uno",
            max_amount=500000,
            weekly_cap_amount=150000,
            min_threshold=10000,
        )
        self.migrar()
        c = self.cuenta("alias.uno")
        self.assertEqual(c.estado, "activa")
        self.assertEqual(
            (c.tope_mensual, c.tope_semanal, c.minimo_por_pago),
            (D("500000.00"), D("150000.00"), D("10000.00")),
        )

    def test_tope_semanal_cero_queda_en_cero(self):
        self.fila("Tincho", alias="a.cero", weekly_cap_amount=0)
        self.migrar()
        self.assertEqual(self.cuenta("a.cero").tope_semanal, D("0.00"))

    def test_limites_tope_uno_y_minimo_cero(self):
        self.fila("Tincho", alias="a.limite", max_amount=1, min_threshold=0)
        self.migrar()
        c = self.cuenta("a.limite")
        self.assertEqual((c.tope_mensual, c.minimo_por_pago), (D("1.00"), D("0.00")))

    def test_minimo_igual_al_tope_pasa(self):
        self.fila("Tincho", alias="a.igual", max_amount=100, min_threshold=100)
        self.migrar()
        self.assertEqual(self.cuenta("a.igual").minimo_por_pago, D("100.00"))

    def test_alias_y_cbu_sin_espacios_y_vacios_a_none(self):
        self.fila("Tincho", alias="  con.espacios ", cbu="0000000000000000000001")
        self.fila("Otro", alias=None, cbu="0000000000000000000002")
        self.migrar()
        self.assertTrue(Cuenta.objects.filter(alias="con.espacios", cbu="0000000000000000000001"))
        self.assertTrue(Cuenta.objects.filter(alias=None, cbu="0000000000000000000002"))


class ListaTest(CuentasBase):
    def posiciones(self):
        return {c.alias: c.posicion for c in Cuenta.objects.all()}

    def test_lista_sin_huecos_con_una_inactiva_en_el_medio(self):
        self.fila("A", alias="alias.uno", priority_order=1)
        self.fila("B", alias="jimena.roble.rana", priority_order=2, is_active=False)
        self.fila("C", alias="rodrigo.mp", priority_order=3)
        self.fila("D", alias="martin.piedra.casa", priority_order=4)
        self.migrar()
        self.assertEqual(
            self.posiciones(),
            {
                "alias.uno": 1,
                "jimena.roble.rana": None,
                "rodrigo.mp": 2,
                "martin.piedra.casa": 3,
            },
        )
        self.assertEqual(Cuenta.objects.get(alias="jimena.roble.rana").estado, "inactiva")

    def test_cuatro_activas_y_una_inactiva_dan_cinco_cuentas_y_lista_de_cuatro(self):
        for i in range(1, 6):
            self.fila(f"N{i}", alias=f"a.{i}", priority_order=i, is_active=i != 3)
        self.migrar()
        self.assertEqual(Cuenta.objects.count(), 5)
        self.assertEqual(
            list(
                Cuenta.objects.filter(posicion__isnull=False)
                .order_by("posicion")
                .values_list("alias", flat=True)
            ),
            ["a.1", "a.2", "a.4", "a.5"],
        )

    def test_huecos_de_prod_se_compactan(self):
        for i, p in enumerate((9, 2, 5), start=1):
            self.fila(f"N{i}", alias=f"a.{i}", priority_order=p)
        self.migrar()
        self.assertEqual(self.posiciones(), {"a.2": 1, "a.3": 2, "a.1": 3})

    def test_empate_de_prioridad_desempata_el_pk(self):
        self.fila("N1", alias="a.1", priority_order=1)
        self.fila("N2", alias="a.2", priority_order=1)
        self.migrar()
        self.assertEqual(self.posiciones(), {"a.1": 1, "a.2": 2})


class DestinatariosTest(CuentasBase):
    def test_sena_pasa_archivada_sin_posicion(self):
        self.fila(
            "Solo para señas", alias="solo.senas", recipient_type="DEPOSIT_ONLY", max_amount=1
        )
        self.migrar()
        c = self.cuenta("solo.senas")
        self.assertEqual((c.estado, c.posicion), ("archivada", None))
        self.assertIsNotNone(c.fecha_archivado)
        self.assertEqual(c.tope_mensual, D("1.00"))

    def test_grupo_con_activa_e_inactiva_es_activo_y_cada_cuenta_su_estado(self):
        viejo = datetime(2026, 9, 1, tzinfo=ARG)
        self.fila("Dr Pérez", alias="p.inactiva", is_active=False, created_at=viejo)
        self.fila("dr  pérez", alias="p.activa", is_active=True)
        self.migrar()
        d = Destinatario.objects.get()
        self.assertEqual((d.estado, d.nombre), ("activo", "Dr Pérez"))
        self.assertEqual(d.fecha_activacion, viejo)
        self.assertEqual(self.cuenta("p.activa").estado, "activa")
        self.assertEqual(self.cuenta("p.inactiva").estado, "inactiva")

    def test_grupo_solo_con_inactivas_es_inactivo(self):
        self.fila("Dr Pérez", alias="p.1", is_active=False)
        self.migrar()
        d = Destinatario.objects.get()
        self.assertEqual((d.estado, d.fecha_activacion), ("inactivo", None))

    def test_tipo_de_los_datos_o_profesional_por_defecto(self):
        self.fila("Proveedor SA", alias="p.1")
        self.fila("Dr Pérez", alias="p.2")
        self.migrar(tipos={"proveedor sa": "proveedor"})
        tipos = dict(Destinatario.objects.values_list("nombre", "tipo"))
        self.assertEqual(tipos, {"Proveedor SA": "proveedor", "Dr Pérez": "profesional"})

    def test_cada_destinatario_y_cuenta_tiene_un_evento_de_alta_de_migracion(self):
        self.fila("Dr Pérez", alias="p.1")
        self.fila("Otro", alias="p.2")
        self.migrar()
        for modelo, n in ((Destinatario, 2), (Cuenta, 2)):
            etiqueta = modelo._meta.label_lower
            eventos = Evento.objects.filter(entidad=etiqueta, tipo="alta")
            self.assertEqual(eventos.count(), n)
            self.assertEqual(
                set(eventos.values_list("origen", "usuario")), {("migración de prod", None)}
            )


class TopesVigentesTest(CuentasBase):
    def setUp(self):
        super().setUp()
        self.op = f.usuario("operador1")
        self.dr = f.usuario("drperez", role="professional")

    def topes(self, cuenta):
        return {t.tipo: t for t in TopeVigente.objects.filter(cuenta=cuenta)}

    def test_desde_es_el_cobro_migrado_mas_antiguo(self):
        fila = self.fila("Tincho", alias="alias.uno", max_amount=500000, weekly_cap_amount=150000)
        primero = datetime(2026, 9, 5, 10, 0, tzinfo=ARG)
        f.pago(fila, 1000, self.op, self.dr, created_at=datetime(2026, 10, 10, 14, 5, tzinfo=ARG))
        f.pago(fila, 1000, self.op, self.dr, created_at=primero)
        self.migrar()
        topes = self.topes(self.cuenta("alias.uno"))
        self.assertEqual(len(topes), 2)
        self.assertEqual(topes["mensual"].monto, D("500000.00"))
        self.assertEqual(topes["semanal"].monto, D("150000.00"))
        self.assertEqual((topes["mensual"].desde, topes["semanal"].desde), (primero, primero))

    def test_tope_de_en_periodos_migrados_y_anteriores_al_primer_pago(self):
        fila = self.fila("Tincho", alias="a.tope", max_amount=500000, weekly_cap_amount=150000)
        f.pago(fila, 1000, self.op, self.dr, created_at=datetime(2026, 9, 5, 10, 0, tzinfo=ARG))
        self.migrar()
        c = Cuenta.objects.prefetch_related("topes").get(alias="a.tope")
        for momento in (datetime(2026, 9, 10, tzinfo=ARG), datetime(2026, 1, 10, tzinfo=ARG)):
            self.assertEqual(tope_de(c, "mensual", momento), D("500000.00"))
            self.assertEqual(tope_de(c, "semanal", momento), D("150000.00"))

    def test_cuenta_sin_cobros_desde_el_alta(self):
        self.fila("Tincho", alias="a.sin.cobros")
        self.migrar()
        c = self.cuenta("a.sin.cobros")
        self.assertEqual({t.desde for t in self.topes(c).values()}, {c.fecha_alta})

    def test_tope_semanal_cero_queda_registrado(self):
        self.fila("Tincho", alias="a.cero", weekly_cap_amount=0)
        self.migrar()
        self.assertEqual(self.topes(self.cuenta("a.cero"))["semanal"].monto, D("0.00"))

    def test_cuenta_de_senas_tiene_tope_mensual_de_un_peso(self):
        self.fila("Señas", alias="solo.senas", recipient_type="DEPOSIT_ONLY", max_amount=1)
        self.migrar()
        self.assertEqual(self.topes(self.cuenta("solo.senas"))["mensual"].monto, D("1.00"))

    def test_los_topes_no_generan_eventos(self):
        self.fila("Tincho", alias="a.sin.evento")
        self.migrar()
        self.assertFalse(Evento.objects.filter(entidad="TopeVigente").exists())

    def test_revision_no_deja_topes(self):
        self.fila("Tincho", alias="a.revision")
        datos = {"para_completar_pagos": "completar", "tipos": {}, "vinculos": {}}
        servicios.migrar(datos, definitiva=False)
        self.assertEqual(TopeVigente.objects.count(), 0)
