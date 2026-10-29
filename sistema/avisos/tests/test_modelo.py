from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from sistema.avisos.models import ConfiguracionAvisos, DireccionAviso
from sistema.avisos.tests import AvisosTestCase


class ConfiguracionAvisosTests(AvisosTestCase):
    def setUp(self):
        super().setUp()

    def test_actual_crea_con_margen_10(self):
        self.assertEqual(ConfiguracionAvisos.actual().margen, 10)

    def test_margen_99_pasa_full_clean(self):
        ConfiguracionAvisos(pk=1, margen=99).full_clean()

    def test_margen_100_falla_full_clean_con_mensaje(self):
        with self.assertRaises(ValidationError) as cm:
            ConfiguracionAvisos(pk=1, margen=100).full_clean()
        self.assertIn("El margen debe ser de 0 % a 99 %.", str(cm.exception))

    def test_margen_0_pasa_full_clean(self):
        ConfiguracionAvisos(pk=1, margen=0).full_clean()

    def test_margen_100_en_base_falla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracionAvisos.objects.create(pk=1, margen=100)

    def test_segunda_configuracion_falla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracionAvisos.objects.create(pk=2)


class DireccionAvisoTests(AvisosTestCase):
    def setUp(self):
        super().setUp()

    def test_email_sin_dominio_valido_falla(self):
        with self.assertRaises(ValidationError) as cm:
            DireccionAviso(email="duenio@clinica").full_clean()
        self.assertIn("Escribí una dirección de email válida.", str(cm.exception))

    def test_full_clean_normaliza_espacios_y_mayusculas(self):
        d = DireccionAviso(email=" Admin@Clinica.test ")
        d.full_clean()
        self.assertEqual(d.email, "admin@clinica.test")

    def test_misma_direccion_con_otra_capitalizacion_falla_en_base(self):
        DireccionAviso.objects.create(email="admin@clinica.test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            DireccionAviso.objects.create(email="ADMIN@clinica.test")
