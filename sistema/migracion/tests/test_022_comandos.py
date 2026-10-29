"""022 FR-032: textos de los comandos de migración."""

from django.core.management.base import CommandError

from sistema.migracion.tests import fabricas as f
from sistema.migracion.tests.test_comando import ComandoBase
from sistema.migracion.tests.test_configuracion import ConfiguracionBase


class MigrarProdTest(ComandoBase):
    def test_segunda_corrida_sin_definitiva_avisa_y_no_muestra_dict(self):
        f.pago(self.fila, 1000, self.ana, self.perez)
        self.correr(definitiva=True)
        with self.assertRaises(CommandError) as ctx:
            self.correr()
        self.assertEqual(
            str(ctx.exception),
            "El sistema nuevo ya tiene datos. La migración corre una sola vez.",
        )
        self.assertNotIn("{", str(ctx.exception))

    def test_segunda_corrida_definitiva_tambien_avisa(self):
        f.pago(self.fila, 1000, self.ana, self.perez)
        self.correr(definitiva=True)
        with self.assertRaisesMessage(CommandError, "La migración corre una sola vez."):
            self.correr(definitiva=True)


class CargarConfiguracionTest(ConfiguracionBase):
    def test_singular(self):
        salida = self.correr()
        for texto in (
            "1 terminal,",
            "1 cuenta de terminal POS",
            "1 sesión",
            "1 dirección de aviso",
        ):
            self.assertIn(texto, salida)
        self.assertNotIn("terminales", salida)

    def test_plural_con_dos_terminales(self):
        conf = self.conf()
        conf["terminales"].append(
            {"nombre": "Payway 2", "proveedor": "Payway", "metodos": ["credito"]}
        )
        self.assertIn("2 terminales", self.correr(conf))
