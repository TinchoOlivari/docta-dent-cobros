from decimal import Decimal as D
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from sistema.auditoria.models import Evento
from sistema.cobros.models import Cobro
from sistema.cobros.tests.fabrica import cobro_prueba
from sistema.destinatarios.models import Cuenta, Destinatario
from sistema.migracion.tests import fabricas as f
from sistema.migracion.tests.test_comando import ComandoBase


class VerificarComandoTest(ComandoBase):
    def setUp(self):
        super().setUp()
        for _ in range(3):
            f.pago(self.fila, 1000, self.ana, self.perez)

    def verificar(self):
        salida = StringIO()
        try:
            call_command("migrar_prod", datos=str(self.ruta), verificar=True, stdout=salida)
        finally:
            self.salida = salida.getvalue()

    def test_verificar_con_datos_sin_migrar_da_error(self):
        with self.assertRaises(CommandError):
            self.verificar()
        self.assertIn("Cobros: prod 3, nuevo 0", self.salida)

    def test_verificar_despues_de_la_definitiva_da_0_diferencias(self):
        self.correr(definitiva=True)
        self.verificar()
        self.assertIn("Verificación: 0 diferencias", self.salida)

    def test_verificar_no_escribe(self):
        self.correr(definitiva=True)
        antes = (Cobro.objects.count(), Evento.objects.count())
        self.verificar()
        self.assertEqual((Cobro.objects.count(), Evento.objects.count()), antes)

    def test_verificar_y_definitiva_juntos_da_error(self):
        with self.assertRaisesMessage(CommandError, "no los dos"):
            call_command(
                "migrar_prod",
                datos=str(self.ruta),
                verificar=True,
                definitiva=True,
                stdout=StringIO(),
            )
        self.assertFalse(Cobro.objects.exists())


class PuedeVolverTest(ComandoBase):
    def nuevo(self, **kw):
        dest = Destinatario.objects.get_or_create(
            nombre="Test", defaults={"tipo": "clinica", "cuit": "20000000001"}
        )[0]
        cuenta = Cuenta.objects.get_or_create(
            destinatario=dest,
            alias="alias.nuevo",
            defaults={
                "tipo": "transferencia",
                "tope_mensual": D("1000000.00"),
                "tope_semanal": D("0.00"),
                "minimo_por_pago": D("0.00"),
            },
        )[0]
        return cobro_prueba(cuenta=cuenta, monto=D("1.00"), **kw)

    def volver(self):
        salida = StringIO()
        call_command("puede_volver", stdout=salida)
        return salida.getvalue()

    def test_puede_volver_con_0_cobros_nuevos(self):
        f.pago(self.fila, 1000, self.ana, self.perez)
        self.correr(definitiva=True)
        self.assertEqual(Cobro.objects.filter(migrado=True).count(), 1)
        self.assertIn(
            "Cobros cargados en el sistema nuevo: 0. Se puede volver atrás.", self.volver()
        )

    def test_puede_volver_con_1_cobro_nuevo_se_niega(self):
        self.nuevo()
        with self.assertRaisesMessage(
            CommandError, "Cobros cargados en el sistema nuevo: 1. No se vuelve atrás (FR-014)."
        ) as error:
            self.volver()
        # corte.sh solo levanta el servicio con este código; otro error lo deja parado.
        self.assertEqual(error.exception.returncode, 3)

    def test_puede_volver_con_2_cobros_nuevos_dice_2(self):
        self.nuevo()
        self.nuevo()
        with self.assertRaisesMessage(CommandError, "sistema nuevo: 2."):
            self.volver()

    def test_puede_volver_cuenta_un_cobro_nuevo_cancelado(self):
        self.nuevo(estado="cancelado")
        with self.assertRaisesMessage(CommandError, "sistema nuevo: 1."):
            self.volver()
