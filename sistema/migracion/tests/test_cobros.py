from datetime import datetime, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.core import mail
from django.utils import timezone

from sistema.cobros.models import Cobro, ItemCobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.core.permisos import VENTANA_OPERADOR, filtro_cobros_visibles
from sistema.core.tests import SistemaTestCase
from sistema.core.tiempo import ARG, rango_mes_arg
from sistema.dentalink.models import ConfiguracionDentalink
from sistema.migracion import servicios
from sistema.migracion.tests import fabricas as f

D10 = datetime(2026, 10, 10, 14, 5, tzinfo=ARG)


class CobrosBase(SistemaTestCase):
    def setUp(self):
        super().setUp()
        f.usuario("completar", role="professional")
        self.ana = f.usuario("ana")
        self.perez = f.usuario(
            "drperez", role="professional", first_name="Pedro", last_name="Pérez"
        )
        self.fila = f.fila("Tincho", alias="alias.uno", max_amount=10000)
        self.sena = f.fila(
            "Señas", alias="senas.prueba", recipient_type="DEPOSIT_ONLY", is_active=False
        )

    def pago(self, amount=15000, fila=None, **kw):
        return f.pago(fila or self.fila, amount, self.ana, self.perez, **kw)

    def datos(self, vinculos=None):
        return {"para_completar_pagos": "completar", "tipos": {}, "vinculos": vinculos or {}}

    def migrar(self, vinculos=None):
        servicios.migrar(self.datos(vinculos), definitiva=True)


class CobroTest(CobrosBase):
    def test_cobro_regular_pasa_con_item_historico(self):
        p = self.pago(archivo="c4567.pdf")
        self.migrar()
        c = Cobro.objects.get(pk=p.pk)
        self.assertEqual(
            (c.estado, c.metodo, c.monto), ("finalizado", "transferencia", D("15000.00"))
        )
        self.assertEqual(c.cuenta.alias, "alias.uno")
        self.assertEqual(c.destinatario, c.cuenta.destinatario)
        self.assertEqual(c.fecha, D10)
        self.assertEqual(c.creado_por, self.ana)
        self.assertEqual(c.comprobante.name, p.proof_of_payment_file.name)
        self.assertEqual(c.nombre_paciente, "Juan Paciente")
        self.assertTrue(c.migrado)
        (i,) = c.items.all()
        self.assertEqual((i.numero, i.monto, i.estado_envio), (1, D("15000.00"), "historico"))
        self.assertEqual(i.nombre_profesional, "Pedro Pérez")
        self.assertFalse(i.saldo_a_favor)

    def test_nombre_profesional_cae_en_username(self):
        f.usuario("sinnombre", role="professional")
        f.pago(self.fila, 100, self.ana, User_get("sinnombre"))
        self.migrar()
        self.assertEqual(ItemCobro.objects.get().nombre_profesional, "sinnombre")

    def test_id_profesional_con_y_sin_vinculo(self):
        self.pago()
        self.migrar({"drperez": {"id": 12, "nombre": "Dr. Pérez"}})
        self.assertEqual(ItemCobro.objects.get().id_profesional, 12)

    def test_id_profesional_sin_vinculo_es_none(self):
        self.pago()
        self.migrar()
        self.assertIsNone(ItemCobro.objects.get().id_profesional)

    def test_revisado_pasa_sin_cambios(self):
        a = self.pago(reviewed=True)
        b = self.pago(reviewed=False)
        self.migrar()
        self.assertTrue(Cobro.objects.get(pk=a.pk).revisado)
        self.assertFalse(Cobro.objects.get(pk=b.pk).revisado)

    def test_ningun_cobro_es_excedente(self):
        self.pago(6000)
        self.pago(6000)
        self.migrar()
        self.assertFalse(Cobro.objects.filter(excedente=True).exists())

    def test_observaciones_none_pasa_a_vacio(self):
        self.pago(observaciones=None)
        self.migrar()
        self.assertEqual(Cobro.objects.get().observaciones, "")

    def test_sin_comprobante_se_guarda_vacio(self):
        self.pago()
        self.migrar()
        self.assertEqual(Cobro.objects.get().comprobante.name, "")

    def test_comprobante_que_no_esta_en_disco_queda_vacio_y_se_informa(self):
        p = self.pago(archivo="falta.pdf")
        from django.core.files.storage import default_storage

        default_storage.delete(p.proof_of_payment_file.name)
        informe = servicios.revisar(self.datos())
        self.assertEqual(informe.sin_comprobante, [p.pk])
        self.migrar()
        self.assertEqual(Cobro.objects.get().comprobante.name, "")

    def test_pagos_del_usuario_para_completar_no_pasan_aca(self):
        f.pago(self.fila, 500, self.ana, User_get("completar"))
        self.pago()
        self.migrar()
        self.assertEqual(Cobro.objects.count(), 1)

    def test_borde_fin_de_mes_cae_en_su_mes(self):
        p = self.pago(created_at=datetime(2026, 10, 31, 23, 59, tzinfo=ARG))
        self.migrar()
        inicio, fin = rango_mes_arg(datetime(2026, 10, 15, tzinfo=ARG))
        self.assertTrue(inicio <= Cobro.objects.get(pk=p.pk).fecha < fin)

    def test_evento_de_alta_por_cobro_e_item(self):
        from sistema.auditoria.models import Evento

        self.pago()
        self.migrar()
        for etiqueta in ("cobros.cobro", "cobros.itemcobro"):
            e = Evento.objects.get(entidad=etiqueta)
            self.assertEqual((e.tipo, e.origen, e.usuario), ("alta", "migración de prod", None))


class SenaTest(CobrosBase):
    def test_sena_va_a_la_cuenta_archivada_con_saldo_a_favor(self):
        self.pago(20000, fila=self.sena, payment_type="DEPOSIT")
        self.migrar()
        c = Cobro.objects.get()
        self.assertEqual(
            (c.cuenta.estado, c.estado, c.monto), ("archivada", "finalizado", D("20000.00"))
        )
        (i,) = c.items.all()
        self.assertTrue(i.saldo_a_favor)
        self.assertEqual((i.monto, i.nombre_profesional), (D("20000.00"), "Pedro Pérez"))
        self.assertIsNone(i.id_prestacion)
        self.assertEqual(i.nombre_prestacion, "Saldo a favor")

    def test_sena_toma_la_prestacion_configurada(self):
        ConfiguracionDentalink.objects.update_or_create(
            pk=1,
            defaults={
                "id_prestacion_saldo_a_favor": 900,
                "nombre_prestacion_saldo_a_favor": "Saldo a favor DL",
            },
        )
        self.pago(20000, fila=self.sena, payment_type="DEPOSIT")
        self.migrar()
        i = ItemCobro.objects.get()
        self.assertEqual((i.id_prestacion, i.nombre_prestacion), (900, "Saldo a favor DL"))


class SinEnviosTest(CobrosBase):
    def test_migrar_no_envia_ni_avisa(self):
        from sistema.avisos.models import DireccionAviso
        from sistema.dentalink import tasks

        DireccionAviso.objects.create(email="aviso@ejemplo.test")
        self.pago()
        self.pago(20000, fila=self.sena, payment_type="DEPOSIT")
        with (
            patch("sistema.dentalink.tasks.enviar_cobro.delay") as enviar,
            patch("sistema.dentalink.tasks.enviar_item") as item,
            patch("sistema.avisos.tasks.enviar_aviso.delay") as aviso,
        ):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self.migrar()
            tasks.enviar_pendientes()
        self.assertEqual(callbacks, [])
        self.assertEqual(enviar.call_count, 0)
        self.assertEqual(item.call_count, 0)
        self.assertEqual(aviso.call_count, 0)
        self.assertFalse(ItemCobro.objects.exclude(estado_envio="historico").exists())

    def test_control_enviar_pendientes_envia_un_item_pendiente(self):
        # Control: el mismo setup detecta un envío real si hay un ítem pendiente (FR-004).
        from sistema.dentalink import tasks

        self.pago()
        self.migrar()
        cobro = Cobro.objects.get()
        ItemCobro.objects.create(
            cobro=cobro, numero=2, id_prestacion=9, monto=D("1.00"), estado_envio="pendiente"
        )
        with patch("sistema.dentalink.tasks.enviar_item") as item:
            tasks.enviar_pendientes()
        self.assertEqual(item.call_count, 1)

    def test_migrar_no_avisa_cerca_del_tope_con_direccion_cargada(self):
        # US3.6: tope 10000, 2 pagos de 6000 pasan el tope; no sale aviso.
        from sistema.avisos.models import DireccionAviso

        DireccionAviso.objects.create(email="aviso@ejemplo.test")
        self.pago(6000)
        self.pago(6000)
        with (
            patch("sistema.avisos.tasks.enviar_aviso.delay") as aviso,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            self.migrar()
        self.assertEqual(callbacks, [])
        self.assertEqual(aviso.call_count, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_control_aviso_cerca_del_tope_sale_con_la_misma_direccion(self):
        from sistema.avisos import servicios as avisos
        from sistema.avisos.models import DireccionAviso

        DireccionAviso.objects.create(email="aviso@ejemplo.test")
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            avisos._enviar("Asunto", ["Cuerpo"])
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_control_encolar_envio_si_dispara_el_callback(self):
        # Sin este control, el test de arriba pasaría aunque el patch no sirviera.
        from sistema.dentalink import servicios as dentalink

        self.pago()
        self.migrar()
        cobro = Cobro.objects.get()
        with (
            patch("sistema.dentalink.tasks.enviar_cobro.delay") as enviar,
            patch.object(dentalink, "cobro_listo", return_value=True),
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            dentalink.encolar_envio(cobro)
        self.assertEqual(len(callbacks), 1)
        enviar.assert_called_once_with(cobro.pk)


class TotalesTest(CobrosBase):
    def test_totales_por_cuenta_iguales_a_prod(self):
        for _ in range(10):
            self.pago(3000)
        for _ in range(2):
            self.pago(2000, fila=self.sena, payment_type="DEPOSIT")
        antes = servicios.revisar(self.datos()).montos
        self.migrar()
        self.assertEqual(Cobro.objects.count(), 12)
        self.assertEqual(sum(c.monto for c in Cobro.objects.all()), D("34000.00"))
        self.assertEqual(sum(antes.values()), D("34000.00"))
        self.assertEqual(sum(i.monto for i in ItemCobro.objects.all()), D("34000.00"))

    def test_operador_no_ve_cobros_migrados_de_mas_de_15_dias(self):
        ahora = timezone.now()
        viejo = self.pago(created_at=ahora - VENTANA_OPERADOR - timedelta(minutes=1))
        nuevo = self.pago(created_at=ahora - VENTANA_OPERADOR + timedelta(minutes=1))
        self.migrar()
        self.assertEqual(Cobro.objects.get(pk=viejo.pk).fecha_alta, viejo.created_at)
        visibles = Cobro.objects.filter(filtro_cobros_visibles(self.ana, ahora))
        self.assertEqual(list(visibles.values_list("pk", flat=True)), [nuevo.pk])

    def test_revision_no_deja_cobros(self):
        self.pago()
        servicios.migrar(self.datos(), definitiva=False)
        self.assertFalse(Cobro.objects.exists())


class SecuenciaTest(CobrosBase):
    def test_cobro_nuevo_tiene_numero_mayor_al_migrado(self):
        p = self.pago()
        Payment_pk(p, 4567)
        self.migrar()
        cuenta = Cobro.objects.get(pk=4567).cuenta
        nuevo = cobro_prueba(cuenta=cuenta, monto=D("100.00"), estado="pendiente")
        self.assertGreater(nuevo.pk, 4567)


def User_get(username):
    from payment_instructions.models import User

    return User.objects.get(username=username)


def Payment_pk(pago, pk):
    from payment_instructions.models import Payment

    Payment.objects.filter(pk=pago.pk).update(id=pk)
    pago.pk = pk
