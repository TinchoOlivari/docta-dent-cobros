from datetime import datetime
from decimal import Decimal as D

from unittest.mock import patch

from django.core.management.base import CommandError
from django.db import IntegrityError

from payment_instructions.models import (
    AutomationRule,
    Payment,
    PaymentRecipient,
    RuleExecutionLog,
    User,
)
from sistema.auditoria.models import Evento
from sistema.cobros.models import Cobro, PagoComplementario
from sistema.automatizacion.models import Regla
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG
from sistema.destinatarios.models import Cuenta, Destinatario, TopeVigente
from sistema.usuarios.models import Perfil
from sistema.migracion import servicios
from sistema.migracion.tests import fabricas as f

CBU1 = "0000000000000000000001"
CBU2 = "0000000000000000000002"


class RevisionBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.completar = f.usuario("completar", role="professional")
        self.op = f.usuario("operador1")
        self.dr = f.usuario("drperez", role="professional")

    def datos(self, **cambios):
        base = {"para_completar_pagos": "completar", "tipos": {}, "vinculos": {}}
        return base | cambios

    def revisar(self, **cambios):
        return servicios.revisar(self.datos(**cambios))


class GruposTest(RevisionBase):
    def test_filas_con_el_mismo_nombre_normalizado_forman_un_grupo(self):
        f.fila("Destinatario Ejemplo", alias="alias.uno")
        f.fila(
            "destinatario  ejemplo",
            alias="martin.piedra.casa",
            is_active=False,
            created_at=datetime(2026, 10, 11, 9, 0, tzinfo=ARG),
        )
        informe = self.revisar()
        self.assertEqual(
            informe.grupos,
            {"Destinatario Ejemplo": ["alias.uno (Activa)", "martin.piedra.casa (Inactiva)"]},
        )

    def test_el_nombre_del_grupo_sale_sin_espacios_en_los_bordes(self):
        f.fila(" Destinatario Ejemplo ", alias="a1")
        self.assertEqual(list(self.revisar().grupos), ["Destinatario Ejemplo"])

    def test_fila_de_senas_figura_archivada(self):
        f.fila("Seña", alias="sena.1", recipient_type="DEPOSIT_ONLY")
        self.assertEqual(self.revisar().grupos, {"Seña": ["sena.1 (Archivada)"]})

    def test_sin_filas_no_hay_grupos(self):
        self.assertEqual(self.revisar().grupos, {})


class ConflictosTest(RevisionBase):
    def test_alias_que_difiere_solo_en_mayusculas_es_un_conflicto(self):
        a = f.fila("A", alias="Rodrigo.MP")
        b = f.fila("B", alias="rodrigo.mp")
        self.assertEqual(self.revisar().conflictos, [f'Alias "Rodrigo.MP": filas {a.pk} y {b.pk}'])

    def test_alias_distintos_no_son_conflicto(self):
        f.fila("A", alias="rodrigo.mp")
        f.fila("B", alias="rodrigo.mp2")
        self.assertEqual(self.revisar().conflictos, [])

    def test_cbu_igual_tras_strip_es_un_conflicto(self):
        corto = CBU1[1:]
        a = f.fila("A", cbu=corto)
        b = f.fila("B", cbu=" " + corto)
        self.assertEqual(self.revisar().conflictos, [f'CBU "{corto}": filas {a.pk} y {b.pk}'])

    def test_dos_filas_con_cbu_nulo_no_son_conflicto(self):
        f.fila("A", alias="a1")
        f.fila("B", alias="b1")
        self.assertEqual(self.revisar().conflictos, [])

    def test_alias_vacio_no_cuenta(self):
        f.fila("A", alias="", cbu=CBU1)
        f.fila("B", alias=None, cbu=CBU2)
        self.assertEqual(self.revisar().conflictos, [])

    def test_un_conflicto_bloquea(self):
        f.fila("A", alias="x")
        f.fila("B", alias="X")
        self.assertTrue(self.revisar().bloquea)


class VinculosYComprobantesTest(RevisionBase):
    def test_especialista_sin_vinculo_sale(self):
        self.assertEqual(self.revisar().sin_vinculo, ["drperez"])

    def test_especialista_con_vinculo_no_sale(self):
        vinculos = {"drperez": {"id": 12, "nombre": "Dr. Pérez"}}
        self.assertEqual(self.revisar(vinculos=vinculos).sin_vinculo, [])

    def test_comprobante_guardado_no_sale(self):
        fila = f.fila("A", alias="a1")
        f.pago(fila, 1000, self.op, self.dr, archivo="ok.pdf")
        self.assertEqual(self.revisar().sin_comprobante, [])

    def test_comprobante_que_no_esta_en_el_disco_sale_con_su_numero(self):
        fila = f.fila("A", alias="a1")
        pago = f.pago(fila, 1000, self.op, self.dr)
        Payment.objects.filter(pk=pago.pk).update(proof_of_payment_file="comprobantes/falta.pdf")
        self.assertEqual(self.revisar().sin_comprobante, [pago.pk])


class ErroresDeFormaTest(RevisionBase):
    def setUp(self):
        super().setUp()
        self.fila = f.fila("A", alias="a1")

    def test_pago_valido_no_da_errores(self):
        f.pago(self.fila, 1, self.op, self.dr)
        self.assertEqual(self.revisar().errores, [])

    def test_fila_sin_alias_ni_cbu(self):
        otra = f.fila("B")
        self.assertEqual(self.revisar().errores, [f"Fila {otra.pk}: no tiene alias ni CBU"])

    def test_alias_de_61_caracteres_da_error_y_de_60_no(self):
        largo = f.fila("L", alias="a" * 61)
        f.fila("M", alias="b" * 60)
        self.assertEqual(
            self.revisar().errores, [f"Fila {largo.pk}: el alias tiene más de 60 caracteres"]
        )

    def test_cbu_de_21_digitos_o_con_letras_da_error_y_de_22_no(self):
        malos = [f.fila(str(i), cbu=c) for i, c in enumerate(["1" * 21, "a" * 22])]
        f.fila("ok", cbu=CBU1)
        self.assertEqual(
            self.revisar().errores,
            [f"Fila {m.pk}: el CBU no tiene 22 dígitos" for m in malos],
        )

    def test_pago_de_monto_cero_da_error_y_de_uno_no(self):
        cero = f.pago(self.fila, 0, self.op, self.dr)
        f.pago(self.fila, 1, self.op, self.dr)
        self.assertEqual(self.revisar().errores, [f"Cobro {cero.pk}: el monto es 0"])

    def test_pago_sin_destinatario(self):
        pago = f.pago(self.fila, 5, self.op, self.dr)
        Payment.objects.filter(pk=pago.pk).update(payment_recipient=None)
        self.assertEqual(self.revisar().errores, [f"Cobro {pago.pk}: no tiene destinatario"])

    def test_pago_en_efectivo_o_pendiente(self):
        a = f.pago(self.fila, 5, self.op, self.dr)
        b = f.pago(self.fila, 5, self.op, self.dr)
        Payment.objects.filter(pk=a.pk).update(metodo="EFECTIVO")
        Payment.objects.filter(pk=b.pk).update(estado="PENDIENTE")
        self.assertEqual(
            self.revisar().errores,
            [f"Cobro {p}: no es una transferencia completada" for p in (a.pk, b.pk)],
        )

    def test_sena_en_fila_regular(self):
        pago = f.pago(self.fila, 5, self.op, self.dr, payment_type="DEPOSIT")
        self.assertEqual(
            self.revisar().errores, [f"Cobro {pago.pk}: es una seña en un destinatario regular"]
        )

    def test_pago_regular_en_fila_de_senas(self):
        fila = f.fila("S", alias="s1", recipient_type="DEPOSIT_ONLY")
        pago = f.pago(fila, 5, self.op, self.dr)
        self.assertEqual(
            self.revisar().errores,
            [f"Cobro {pago.pk}: es un pago regular en un destinatario de señas"],
        )

    def test_pago_del_usuario_para_completar_pagos_en_fila_de_senas_no_da_error(self):
        fila = f.fila("S", alias="s1", recipient_type="DEPOSIT_ONLY")
        f.pago(fila, 5, self.op, self.completar)
        self.assertEqual(self.revisar().errores, [])

    def test_rol_desconocido(self):
        u = f.usuario("raro", role="supervisor")
        self.assertEqual(
            self.revisar().errores,
            [f'Usuario "{u.username}": el rol "supervisor" no existe en el sistema nuevo'],
        )


class TotalesYMontosTest(RevisionBase):
    def test_totales_separan_cobros_senas_y_complementarios(self):
        fila = f.fila("A", alias="alias.uno")
        sena = f.fila("S", alias="sena.1", recipient_type="DEPOSIT_ONLY")
        for _ in range(3):
            f.pago(fila, 100, self.op, self.dr)
        f.pago(sena, 50, self.op, self.dr, payment_type="DEPOSIT")
        f.pago(fila, 70, self.op, self.completar)
        informe = self.revisar()
        t = informe.totales
        self.assertEqual((t["cobros"], t["senas"], t["pagos_complementarios"]), (3, 1, 1))
        self.assertEqual((t["cuentas"], t["usuarios"]), (2, 2))
        self.assertEqual((t["reglas"], t["ejecuciones"]), (0, 0))
        self.assertEqual(informe.errores, [])

    def test_reglas_y_ejecuciones_se_cuentan(self):
        fila = f.fila("A", alias="a1")
        regla = f.regla(fila)
        f.ejecucion(regla)
        f.ejecucion(regla)
        t = self.revisar().totales
        self.assertEqual((t["reglas"], t["ejecuciones"]), (1, 2))

    def test_montos_por_cuenta_y_mes(self):
        fila = f.fila("A", alias="alias.uno")
        octubre = datetime(2026, 10, 5, 12, 0, tzinfo=ARG)
        f.pago(fila, 15000, self.op, self.dr, created_at=octubre)
        f.pago(fila, 15000, self.op, self.completar, created_at=octubre)
        f.pago(fila, 1, self.op, self.dr, created_at=datetime(2026, 11, 2, 9, 0, tzinfo=ARG))
        montos = self.revisar().montos
        self.assertEqual(
            montos,
            {
                ("alias.uno", "2026-10"): D("30000.00"),
                ("alias.uno", "2026-11"): D("1.00"),
            },
        )
        self.assertTrue(all(isinstance(m, D) for m in montos.values()))

    def test_limite_de_mes_en_hora_argentina(self):
        fila = f.fila("A", alias="a1")
        f.pago(
            fila, 10, self.op, self.dr, created_at=datetime(2026, 10, 31, 23, 59, 59, tzinfo=ARG)
        )
        f.pago(fila, 20, self.op, self.dr, created_at=datetime(2026, 11, 1, 0, 0, 0, tzinfo=ARG))
        f.pago(fila, 40, self.op, self.dr, created_at=datetime(2026, 10, 1, 0, 0, 0, tzinfo=ARG))
        self.assertEqual(
            self.revisar().montos,
            {("a1", "2026-10"): D("50.00"), ("a1", "2026-11"): D("20.00")},
        )

    def test_cuenta_sin_alias_se_identifica_por_cbu(self):
        fila = f.fila("A", cbu=CBU1)
        f.pago(fila, 7, self.op, self.dr)
        self.assertEqual(self.revisar().montos, {(CBU1, "2026-10"): D("7.00")})

    def test_el_texto_muestra_los_montos_con_formato_de_pesos(self):
        fila = f.fila("A", alias="a1")
        f.pago(fila, 3200000, self.op, self.dr)
        self.assertIn("  a1 2026-10: $3.200.000,00", self.revisar().texto())


class SoloLecturaTest(RevisionBase):
    MODELOS = (
        Destinatario,
        Cuenta,
        Cobro,
        PagoComplementario,
        Regla,
        Evento,
        PaymentRecipient,
        Payment,
        AutomationRule,
        RuleExecutionLog,
        User,
    )

    def cantidades(self):
        return [m.objects.count() for m in self.MODELOS]

    def test_revisar_no_deja_ningun_registro_nuevo(self):
        fila = f.fila("A", alias="alias.uno")
        sena = f.fila("S", alias="sena.1", recipient_type="DEPOSIT_ONLY")
        f.pago(fila, 100, self.op, self.dr)
        f.pago(sena, 50, self.op, self.dr, payment_type="DEPOSIT")
        f.pago(fila, 70, self.op, self.completar)
        f.ejecucion(f.regla(fila))
        antes = self.cantidades()
        self.revisar()
        self.assertEqual(self.cantidades(), antes)
        self.assertEqual(
            [
                m.objects.count()
                for m in (Destinatario, Cuenta, Cobro, PagoComplementario, Regla, Evento)
            ],
            antes[:6],
        )


class MigrarTest(RevisionBase):
    def fila_ok(self, **kw):
        kw.setdefault("alias", "a.uno")
        return f.fila("Tincho", **kw)

    def contar(self):
        return (
            Destinatario.objects.count(),
            Cuenta.objects.filter(tipo="transferencia").count(),
            Evento.objects.count(),
        )

    def test_definitiva_con_conflicto_no_escribe(self):
        self.fila_ok(alias="a.uno")
        f.fila("Otro", alias="A.UNO")
        antes = self.contar()
        with self.assertRaisesMessage(CommandError, "hay conflictos o errores"):
            servicios.migrar(self.datos(), definitiva=True)
        self.assertEqual(self.contar(), antes)

    def test_revision_con_conflicto_devuelve_el_informe_sin_escribir(self):
        self.fila_ok(alias="a.uno")
        f.fila("Otro", alias="A.UNO")
        informe = servicios.migrar(self.datos(), definitiva=False)
        self.assertTrue(informe.bloquea)
        self.assertEqual(Cuenta.objects.count(), 0)

    def test_minimo_mayor_al_tope_deshace_todo(self):
        self.fila_ok(alias="a.uno")
        self.fila_ok(alias="a.dos", min_threshold=600000, max_amount=500000)
        antes = self.contar()
        with self.assertRaises(CommandError):
            servicios.migrar(self.datos(), definitiva=True)
        self.assertEqual(self.contar(), antes)

    def preparar_paso_que_falla(self):
        root = f.usuario("root", role="operator", is_superuser=True)
        fila = self.fila_ok()
        f.pago(fila, 1000, self.op, self.dr)
        f.regla(fila)
        return root

    def test_paso_que_falla_despues_de_escribir_deshace_todo(self):
        # FR-003: _usuarios, _cuentas y _cobros ya escribieron cuando _reglas falla.
        root = self.preparar_paso_que_falla()
        with patch.object(servicios, "_reglas", side_effect=IntegrityError("falla de prueba")):
            with self.assertRaisesMessage(CommandError, "falla de prueba"):
                servicios.migrar(self.datos(), definitiva=True)
        self.assertEqual(
            (
                Destinatario.objects.count(),
                Cuenta.objects.filter(tipo="transferencia").count(),
                Cobro.objects.count(),
                Perfil.objects.count(),
                Evento.objects.count(),
                TopeVigente.objects.count(),
            ),
            (0, 0, 0, 0, 0, 0),
        )
        root.refresh_from_db()
        self.completar.refresh_from_db()
        self.assertEqual(root.role, "operator")
        self.assertTrue(self.completar.is_active)

    def test_sin_el_paso_que_falla_la_definitiva_deja_el_cobro(self):
        # Control del test anterior: los mismos datos, sin falla, sí escriben.
        self.preparar_paso_que_falla()
        servicios.migrar(self.datos(), definitiva=True)
        self.assertEqual(Cobro.objects.count(), 1)

    def test_revisar_lista_la_fila_con_minimo_mayor_al_tope(self):
        malo = self.fila_ok(alias="a.dos", min_threshold=600000, max_amount=500000)
        informe = servicios.migrar(self.datos(), definitiva=False)
        mensajes = [e for e in informe.errores if e.startswith(f"Fila {malo.pk}:")]
        self.assertEqual(len(mensajes), 1)
        self.assertIn("mínimo", mensajes[0])

    def test_minimo_igual_al_tope_no_es_error(self):
        self.fila_ok(alias="a.dos", min_threshold=500000, max_amount=500000)
        self.assertEqual(servicios.revisar(self.datos()).errores, [])

    def test_segunda_definitiva_falla(self):
        self.fila_ok()
        servicios.migrar(self.datos(), definitiva=True)
        with self.assertRaisesMessage(CommandError, "corre una sola vez"):
            servicios.migrar(self.datos(), definitiva=True)

    def test_revision_sin_conflictos_hace_rollback(self):
        self.fila_ok()
        antes = self.contar()
        informe = servicios.migrar(self.datos(), definitiva=False)
        self.assertEqual(informe.modo, "revisión")
        self.assertEqual(self.contar(), antes)

    def test_definitiva_marca_el_modo(self):
        self.fila_ok()
        self.assertEqual(servicios.migrar(self.datos(), definitiva=True).modo, "definitiva")
        self.assertEqual(Cuenta.objects.filter(tipo="transferencia").count(), 1)
