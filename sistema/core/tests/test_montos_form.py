from decimal import Decimal

from django import forms
from django.test import SimpleTestCase

from sistema.cobros.forms import MONTO, MontoDecimalField


class F(forms.Form):
    monto = MontoDecimalField(**MONTO)


class MontoFormTest(SimpleTestCase):
    def test_acepta_formato_argentino_sin_js(self):
        for texto, esperado in (
            ("1.500,50", "1500.50"),
            ("0,01", "0.01"),
            ("0", "0"),
            ("1500,5", "1500.5"),
            ("1.500", "1500"),
            ("1.234.567,89", "1234567.89"),
            ("1.500,55", "1500.55"),
        ):
            with self.subTest(texto=texto):
                f = F({"monto": texto})
                self.assertTrue(f.is_valid(), f.errors)
                self.assertEqual(f.cleaned_data["monto"], Decimal(esperado))

    def test_rechaza_lo_que_rechaza_el_js(self):
        for texto in (
            "1.5",
            "10.99",
            "40000.50",
            "1,5e3",
            "٣",
            "1.500,5,5",
            "1.00.0",
            "1500,",
            "-",
        ):
            with self.subTest(texto=texto):
                f = F({"monto": texto})
                self.assertFalse(f.is_valid())
                self.assertEqual(
                    f.errors["monto"], ["Escribí un monto válido, por ejemplo 1.500,50."]
                )

    def test_mas_de_dos_decimales_y_vacio(self):
        self.assertEqual(F({"monto": "1.500,555"}).errors["monto"], ["Escribí hasta 2 decimales."])
        self.assertEqual(F({"monto": ""}).errors["monto"], ["Este campo es obligatorio."])

    def test_error_en_voseo(self):
        f = F({"monto": "abc"})
        self.assertFalse(f.is_valid())
        self.assertEqual(f.errors["monto"], ["Escribí un monto válido, por ejemplo 1.500,50."])
        f = F({"monto": "1,234"})
        self.assertEqual(f.errors["monto"], ["Escribí hasta 2 decimales."])
