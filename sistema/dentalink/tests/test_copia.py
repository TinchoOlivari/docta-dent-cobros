from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from payment_instructions.models import User
from sistema.auditoria.models import Evento
from sistema.core.tests import SistemaTestCase
from sistema.dentalink import servicios
from sistema.dentalink.cliente import DentalinkNoDisponible
from sistema.dentalink.models import Banco, ConfiguracionDentalink, MedioPago, Profesional
from sistema.destinatarios.forms import CuentaTransferenciaForm
from sistema.usuarios.forms import UsuarioEdicionForm


class ModelosTest(SistemaTestCase):
    def test_id_dentalink_repetido_falla(self):
        Banco.objects.create(id_dentalink=20, nombre="Banco Inventado")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Banco.objects.create(id_dentalink=20, nombre="Otro")

    def test_configuracion_pk_distinto_de_uno_falla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracionDentalink.objects.create(pk=2)

    def test_saldo_a_favor_con_id_y_sin_nombre_falla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracionDentalink.objects.create(pk=1, id_prestacion_saldo_a_favor=9100)

    def test_saldo_a_favor_completo_se_guarda(self):
        config = ConfiguracionDentalink.objects.create(
            pk=1, id_prestacion_saldo_a_favor=9100, nombre_prestacion_saldo_a_favor="Saldo a favor"
        )
        self.assertEqual(config.pk, 1)

    def test_actual_devuelve_siempre_la_fila_uno(self):
        self.assertEqual(ConfiguracionDentalink.actual().pk, 1)
        self.assertEqual(ConfiguracionDentalink.actual().pk, 1)
        self.assertEqual(ConfiguracionDentalink.objects.count(), 1)

    def test_banco_no_se_borra(self):
        with self.assertRaises(ProtectedError):
            Banco.objects.create(id_dentalink=1, nombre="B").delete()


def listas(bancos=(), dentistas=(), medios=()):
    datos = {"bancos": bancos, "dentistas": dentistas, "mediospago": medios}
    return lambda recurso: [{"id": i, "nombre": n} for i, n in datos[recurso]]


class ActualizarCopiaTest(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "a", email="a@example.com", password="x", role="administrator"
        )

    def correr(self, **kw):
        with patch("sistema.dentalink.servicios.cliente") as c:
            c.listar.side_effect = listas(**kw)
            return servicios.actualizar_copia(usuario=self.admin)

    def eventos(self, tipo):
        return Evento.objects.filter(tipo=tipo, entidad="dentalink.banco").count()

    def test_dos_bancos_nuevos_son_dos_altas_con_dos_eventos(self):
        r = self.correr(bancos=[(1, "A"), (2, "B")])
        self.assertEqual(r, {"altas": 2, "cambios": 0, "bajas": 0})
        self.assertEqual(self.eventos("alta"), 2)

    def test_segunda_corrida_igual_no_genera_eventos(self):
        self.correr(bancos=[(1, "A")], dentistas=[(5, "Dr. Y")], medios=[(7, "Efectivo")])
        antes = Evento.objects.count()
        r = self.correr(bancos=[(1, "A")], dentistas=[(5, "Dr. Y")], medios=[(7, "Efectivo")])
        self.assertEqual(r, {"altas": 0, "cambios": 0, "bajas": 0})
        self.assertEqual(Evento.objects.count(), antes)

    def test_banco_renombrado_es_un_cambio(self):
        self.correr(bancos=[(1, "A")])
        r = self.correr(bancos=[(1, "A2")])
        self.assertEqual(r["cambios"], 1)
        self.assertEqual(Banco.objects.get(id_dentalink=1).nombre, "A2")

    def test_banco_que_falta_queda_no_vigente_y_vuelve(self):
        self.correr(bancos=[(1, "A"), (2, "B")])
        r = self.correr(bancos=[(1, "A")])
        self.assertEqual(r["bajas"], 1)
        self.assertFalse(Banco.objects.get(id_dentalink=2).vigente)
        r = self.correr(bancos=[(1, "A"), (2, "B")])
        self.assertEqual(r["cambios"], 1)
        self.assertTrue(Banco.objects.get(id_dentalink=2).vigente)

    def test_falla_una_lista_no_cambia_ninguna_tabla(self):
        def listar(recurso):
            if recurso == "mediospago":
                raise DentalinkNoDisponible()
            return [{"id": 1, "nombre": "A"}]

        with patch("sistema.dentalink.servicios.cliente") as c:
            c.listar.side_effect = listar
            with self.assertRaises(DentalinkNoDisponible):
                servicios.actualizar_copia(usuario=self.admin)
        self.assertEqual(Banco.objects.count() + Profesional.objects.count(), 0)
        self.assertEqual(MedioPago.objects.count(), 0)


class ConfiguracionTest(SistemaTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "a", email="a@example.com", password="x", role="administrator"
        )
        self.banco = Banco.objects.create(id_dentalink=3, nombre="Otro")

    def guardar(self, id_, nombre, banco):
        return servicios.guardar_configuracion(
            usuario=self.admin,
            id_prestacion_saldo_a_favor=id_,
            nombre_prestacion_saldo_a_favor=nombre,
            banco_por_defecto=banco,
        )

    def test_guardar_registra_evento_cambio(self):
        self.guardar(9100, "Saldo a favor", self.banco)
        self.assertEqual(ConfiguracionDentalink.actual().id_prestacion_saldo_a_favor, 9100)
        self.assertTrue(
            Evento.objects.filter(
                tipo="cambio", entidad="dentalink.configuraciondentalink"
            ).exists()
        )

    def test_id_sin_nombre_falla(self):
        with self.assertRaisesMessage(ValidationError, "Cargá el id y el nombre"):
            self.guardar(9100, "", None)

    def test_banco_no_vigente_falla(self):
        self.banco.vigente = False
        self.banco.save()
        with self.assertRaises(ValidationError):
            self.guardar(None, "", self.banco)

    def test_form_de_cuenta_ofrece_bancos_vigentes_y_conserva_el_actual(self):
        Banco.objects.create(id_dentalink=4, nombre="Viejo", vigente=False)
        form = CuentaTransferenciaForm()
        self.assertEqual([c[0] for c in form.fields["banco"].choices], ["", "Otro"])

    def test_form_de_cuenta_sin_bancos_vigentes_sigue_siendo_texto(self):
        Banco.objects.update(vigente=False)
        self.assertEqual(CuentaTransferenciaForm().fields["banco"].widget.input_type, "text")

    def test_form_de_usuario_completa_id_y_nombre_del_profesional(self):
        prof = Profesional.objects.create(id_dentalink=11, nombre="Dra. X")
        form = UsuarioEdicionForm(
            {"role": "professional", "profesional_dentalink": prof.pk}, rol="professional"
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["id_profesional_dentalink"], 11)
        self.assertEqual(form.cleaned_data["nombre_profesional_dentalink"], "Dra. X")
