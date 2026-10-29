import base64
import subprocess
from datetime import date, datetime
from decimal import Decimal
from http.client import RemoteDisconnected
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

from django.test import override_settings

from sistema.arca import cliente
from sistema.arca.cliente import Resultado
from sistema.arca.tests import respuestas
from sistema.arca.tests.fabricas import AHORA, atrapar, emisor_prueba, fugas_en_traceback
from sistema.core.tests import SistemaTestCase

TA = {"token": "TOKEN-DE-PRUEBA", "sign": "SIGN-DE-PRUEBA"}


def pedido(**campos) -> dict:
    """Factura B de $65.000,00 al 21 %: neto 53719.01 + IVA 11280.99."""
    datos = dict(
        cbte_tipo=6,
        punto_venta=3,
        numero=42,
        concepto=2,
        doc_tipo=96,
        doc_numero="30111222",
        fecha=date(2026, 10, 20),
        neto=Decimal("53719.01"),
        iva=Decimal("11280.99"),
        total=Decimal("65000.00"),
        alicuota_id=5,
        servicio_desde=date(2026, 10, 19),
        servicio_hasta=date(2026, 10, 19),
        vto_pago=date(2026, 10, 20),
        asociado=None,
    )
    return datos | campos


class ConexionFalsaMixin:
    """Reemplaza `HTTPSConnection`: ningún test sale a la red."""

    def conexion(self, *, status=200, cuerpo="", **fallas):
        con = mock.MagicMock()
        con.connect.side_effect = fallas.get("connect")
        con.request.side_effect = fallas.get("request")
        con.getresponse.side_effect = fallas.get("getresponse")
        if not fallas.get("getresponse"):
            con.getresponse.return_value.status = status
            con.getresponse.return_value.read.return_value = (
                cuerpo.encode() if isinstance(cuerpo, str) else cuerpo
            )
        parche = mock.patch("sistema.arca.cliente.HTTPSConnection", return_value=con)
        self.clase = parche.start()
        self.addCleanup(parche.stop)
        self.con = con
        return con

    def enviado(self) -> str:
        return self.con.request.call_args.args[2].decode()


class SoapTest(ConexionFalsaMixin, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.url = cliente.URL_WSFE["prueba"]

    def test_connect_con_oserror_es_reintentable_y_no_envia(self):
        con = self.conexion(connect=OSError("dns"))
        r = cliente._soap(self.url, "x", "<a/>", escritura=True)
        self.assertEqual(r.tipo, "reintentable")
        con.request.assert_not_called()

    def test_getresponse_con_timeout_es_dudoso_en_escritura(self):
        self.conexion(getresponse=TimeoutError())
        self.assertEqual(cliente._soap(self.url, "x", "<a/>", escritura=True).tipo, "dudoso")

    def test_corte_despues_de_enviar_es_dudoso_en_escritura(self):
        self.conexion(getresponse=RemoteDisconnected("corte"))
        self.assertEqual(cliente._soap(self.url, "x", "<a/>", escritura=True).tipo, "dudoso")

    def test_error_al_enviar_es_dudoso_en_escritura(self):
        self.conexion(request=BrokenPipeError())
        self.assertEqual(cliente._soap(self.url, "x", "<a/>", escritura=True).tipo, "dudoso")

    def test_timeout_en_lectura_es_reintentable(self):
        self.conexion(getresponse=TimeoutError())
        r = cliente._lectura(cliente._soap(self.url, "x", "<a/>"))
        self.assertEqual(r.tipo, "reintentable")

    def test_timeouts_5_para_conectar_y_30_para_leer(self):
        con = self.conexion(cuerpo=respuestas.ULTIMO_41)
        cliente._soap(self.url, "x", "<a/>")
        self.clase.assert_called_once_with("wswhomo.afip.gov.ar", timeout=5)
        con.sock.settimeout.assert_called_once_with(30)
        con.close.assert_called_once()

    def test_500_sin_soap_es_reintentable_en_lectura_y_dudoso_en_escritura(self):
        self.conexion(status=500, cuerpo="<html>boom</html>")
        self.assertEqual(cliente._soap(self.url, "x", "<a/>").tipo, "reintentable")
        self.assertEqual(cliente._soap(self.url, "x", "<a/>", escritura=True).tipo, "dudoso")

    def test_400_sin_soap_es_reintentable_tambien_en_escritura(self):
        self.conexion(status=400, cuerpo="mal pedido")
        self.assertEqual(cliente._soap(self.url, "x", "<a/>", escritura=True).tipo, "reintentable")

    def test_200_ilegible_es_dudoso_en_escritura(self):
        self.conexion(status=200, cuerpo="no es xml")
        self.assertEqual(cliente._soap(self.url, "x", "<a/>", escritura=True).tipo, "dudoso")

    def test_fault_es_reintentable_con_su_texto(self):
        self.conexion(status=500, cuerpo=respuestas.LOGIN_TA_VALIDO)
        r = cliente._soap(self.url, "x", "<a/>", escritura=True)
        self.assertEqual(r.tipo, "reintentable")
        self.assertIn("TA valido", r.error)

    def test_dtd_en_la_respuesta_se_rechaza(self):
        cuerpo = '<!DOCTYPE a [<!ENTITY e "x">]><a>&e;</a>'
        self.conexion(status=200, cuerpo=cuerpo)
        self.assertEqual(cliente._soap(self.url, "x", "<a/>").tipo, "reintentable")

    def test_ok_lleva_la_raiz_sin_espacios_de_nombres(self):
        self.conexion(cuerpo=respuestas.ULTIMO_41)
        r = cliente._soap(self.url, "x", "<a/>")
        self.assertEqual((r.tipo, r.datos.tag), ("ok", "Envelope"))
        self.assertIsNotNone(r.datos.find(".//CbteNro"))

    def test_el_pedido_sale_en_utf8_con_soapaction(self):
        con = self.conexion(cuerpo=respuestas.ULTIMO_41)
        cliente._soap(self.url, "accion", "<a>ñ</a>")
        metodo, ruta, cuerpo, cabeceras = con.request.call_args.args
        self.assertEqual(
            (metodo, ruta, cuerpo), ("POST", "/wsfev1/service.asmx", "<a>ñ</a>".encode())
        )
        self.assertEqual(cabeceras["SOAPAction"], "accion")

    def test_error_se_corta_a_500_caracteres(self):
        self.assertEqual(len(Resultado("reintentable", error="x" * 2000).error), 500)
        self.assertEqual(len(Resultado("reintentable", error="x" * 500).error), 500)

    def test_repr_del_resultado_no_muestra_los_datos(self):
        self.assertNotIn("secreto", repr(Resultado("ok", {"token": "secreto"})))


class LoginTest(ConexionFalsaMixin, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()

    def test_login_ok_lee_token_sign_y_vencimiento(self):
        self.conexion(cuerpo=respuestas.LOGIN_OK)
        r = cliente.login(self.emisor, ahora=AHORA)
        self.assertEqual(r.tipo, "ok")
        self.assertEqual((r.datos["token"], r.datos["sign"]), ("TOKEN-DE-PRUEBA", "SIGN-DE-PRUEBA"))
        self.assertEqual(r.datos["vence"], datetime(2026, 10, 20, 21, 50, tzinfo=cliente.ARG))
        self.clase.assert_called_once_with("wsaahomo.afip.gov.ar", timeout=5)
        self.assertIn("<wsaa:in0>", self.enviado())

    def test_login_con_ta_valido_es_reintentable_con_el_texto(self):
        self.conexion(status=500, cuerpo=respuestas.LOGIN_TA_VALIDO)
        r = cliente.login(self.emisor, ahora=AHORA)
        self.assertEqual(r.tipo, "reintentable")
        self.assertIn("TA valido", r.error)

    def test_login_con_timeout_es_reintentable(self):
        self.conexion(getresponse=TimeoutError())
        self.assertEqual(cliente.login(self.emisor, ahora=AHORA).tipo, "reintentable")

    def test_login_con_ticket_ilegible_es_reintentable(self):
        self.conexion(
            cuerpo=respuestas.SOBRE.format("<loginCmsResponse><loginCmsReturn/></loginCmsResponse>")
        )
        self.assertEqual(cliente.login(self.emisor, ahora=AHORA).tipo, "reintentable")

    def test_login_con_firma_fallida_es_rechazado(self):
        self.conexion(cuerpo=respuestas.LOGIN_OK)
        self.emisor.clave = "basura"
        r = cliente.login(self.emisor, ahora=AHORA)
        self.assertEqual((r.tipo, r.error), ("rechazado", "No se pudo firmar el pedido a ARCA."))
        self.con.request.assert_not_called()

    def test_login_produccion_bloqueado_si_el_servidor_no_la_permite(self):
        self.conexion(cuerpo=respuestas.LOGIN_OK)
        self.emisor.ambiente = "produccion"
        with override_settings(ARCA_PRODUCCION=False):
            r = cliente.login(self.emisor, ahora=AHORA)
        self.assertEqual((r.tipo, r.error), ("rechazado", cliente.SIN_PRODUCCION))
        self.clase.assert_not_called()

    def test_login_produccion_usa_la_url_de_produccion_si_el_servidor_la_permite(self):
        self.conexion(cuerpo=respuestas.LOGIN_OK)
        self.emisor.ambiente = "produccion"
        with override_settings(ARCA_PRODUCCION=True):
            self.assertEqual(cliente.login(self.emisor, ahora=AHORA).tipo, "ok")
        self.clase.assert_called_once_with("wsaa.afip.gov.ar", timeout=5)


class UltimoAutorizadoTest(ConexionFalsaMixin, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()

    def test_da_el_ultimo_numero(self):
        self.conexion(cuerpo=respuestas.ULTIMO_41)
        r = cliente.ultimo_autorizado(self.emisor, TA, 6)
        self.assertEqual((r.tipo, r.datos), ("ok", {"numero": 41}))
        enviado = self.enviado()
        for esperado in ("<ar:PtoVta>3</ar:PtoVta>", "<ar:CbteTipo>6</ar:CbteTipo>"):
            self.assertIn(esperado, enviado)
        self.assertIn("<ar:Cuit>20000000001</ar:Cuit>", enviado)

    def test_cero_es_un_numero_valido(self):
        self.conexion(cuerpo=respuestas.ULTIMO_41.replace("<CbteNro>41<", "<CbteNro>0<"))
        self.assertEqual(cliente.ultimo_autorizado(self.emisor, TA, 6).datos, {"numero": 0})

    def test_errores_600_601_y_602_piden_ticket_nuevo(self):
        for codigo in ("599", "600", "601", "602", "603"):
            with self.subTest(codigo=codigo):
                cuerpo = respuestas.con_errores("FECompUltimoAutorizado", codigo, "token")
                self.conexion(cuerpo=cuerpo)
                r = cliente.ultimo_autorizado(self.emisor, TA, 6)
                self.assertEqual(r.tipo, "reintentable")
                self.assertEqual(
                    r.datos, {"token_invalido": True} if codigo in "600601602" else None
                )
                self.assertIn(f"{codigo}: token", r.error)

    def test_timeout_es_reintentable(self):
        self.conexion(getresponse=TimeoutError())
        self.assertEqual(cliente.ultimo_autorizado(self.emisor, TA, 6).tipo, "reintentable")


class SolicitarCaeTest(ConexionFalsaMixin, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()

    def solicitar(self, cuerpo=None, **campos):
        if cuerpo is not None:
            self.conexion(cuerpo=cuerpo)
        return cliente.solicitar_cae(self.emisor, TA, pedido(**campos))

    def test_autorizado_da_cae_y_vencimiento(self):
        r = self.solicitar(respuestas.CAE_A)
        self.assertEqual(r.tipo, "ok")
        self.assertEqual(r.datos, {"cae": "75123456789012", "cae_vence": date(2026, 10, 30)})

    def test_rechazado_da_los_codigos_de_observaciones(self):
        r = self.solicitar(respuestas.CAE_R)
        self.assertEqual((r.tipo, r.error), ("rechazado", "10015: Doc. Nro. invalido"))

    def test_error_de_token_sin_cabecera_es_reintentable_con_ticket_invalido(self):
        r = self.solicitar(respuestas.CAE_ERROR_600)
        self.assertEqual((r.tipo, r.datos), ("reintentable", {"token_invalido": True}))

    def test_error_sin_cabecera_es_reintentable(self):
        r = self.solicitar(respuestas.CAE_ERROR_10016)
        self.assertEqual((r.tipo, r.datos), ("reintentable", None))
        self.assertIn("10016", r.error)

    def test_respuesta_sin_resultado_es_dudosa(self):
        vacia = respuestas.SOBRE.format(
            "<FECAESolicitarResponse><FECAESolicitarResult/></FECAESolicitarResponse>"
        )
        self.assertEqual(self.solicitar(vacia).tipo, "dudoso")

    def test_autorizado_sin_cae_es_dudoso(self):
        self.assertEqual(self.solicitar(respuestas.cae("A", cae="")).tipo, "dudoso")

    def test_autorizado_con_vencimiento_ilegible_es_dudoso(self):
        self.assertEqual(self.solicitar(respuestas.cae("A", vence="20261340")).tipo, "dudoso")

    def test_resultado_parcial_es_dudoso(self):
        self.assertEqual(self.solicitar(respuestas.cae("P")).tipo, "dudoso")

    def test_timeout_es_dudoso(self):
        self.conexion(getresponse=TimeoutError())
        self.assertEqual(self.solicitar().tipo, "dudoso")

    def test_500_sin_soap_es_dudoso(self):
        self.conexion(status=500, cuerpo="<html/>")
        self.assertEqual(self.solicitar().tipo, "dudoso")

    def test_connect_caido_es_reintentable_y_no_envia_nada(self):
        con = self.conexion(connect=OSError("sin red"))
        self.assertEqual(self.solicitar().tipo, "reintentable")
        con.request.assert_not_called()

    def test_fault_es_reintentable(self):
        self.conexion(status=500, cuerpo=respuestas.LOGIN_TA_VALIDO)
        self.assertEqual(self.solicitar().tipo, "reintentable")

    def test_produccion_bloqueada_no_envia(self):
        self.conexion(cuerpo=respuestas.CAE_A)
        self.emisor.ambiente = "produccion"
        with override_settings(ARCA_PRODUCCION=False):
            r = self.solicitar()
        self.assertEqual((r.tipo, r.error), ("rechazado", cliente.SIN_PRODUCCION))
        self.clase.assert_not_called()

    def test_xml_de_factura_b_por_65000_al_21(self):
        self.solicitar(respuestas.CAE_A)
        xml = ET.fromstring(self.enviado())
        ns = {"ar": cliente.NS_WSFE}
        det = xml.find(".//ar:FECAEDetRequest", ns)

        def valor(nombre):
            return det.find(f"ar:{nombre}", ns).text

        self.assertEqual(
            [
                valor(n)
                for n in ("ImpTotal", "ImpNeto", "ImpIVA", "ImpTotConc", "ImpOpEx", "ImpTrib")
            ],
            ["65000.00", "53719.01", "11280.99", "0.00", "0.00", "0.00"],
        )
        self.assertEqual((valor("DocTipo"), valor("DocNro")), ("96", "30111222"))
        self.assertEqual(
            [valor(n) for n in ("Concepto", "CbteDesde", "CbteHasta", "CbteFch")],
            ["2", "42", "42", "20261020"],
        )
        self.assertEqual(
            [valor(n) for n in ("MonId", "MonCotiz", "CondicionIVAReceptorId")], ["PES", "1", "5"]
        )
        self.assertEqual(
            [valor(n) for n in ("FchServDesde", "FchServHasta", "FchVtoPago")],
            ["20261019", "20261019", "20261020"],
        )
        alic = det.find("ar:Iva/ar:AlicIva", ns)
        self.assertEqual(
            [alic.find(f"ar:{n}", ns).text for n in ("Id", "BaseImp", "Importe")],
            ["5", "53719.01", "11280.99"],
        )
        self.assertEqual(xml.find(".//ar:FeCabReq/ar:CbteTipo", ns).text, "6")
        self.assertIsNone(det.find("ar:CbtesAsoc", ns))
        self.assertEqual(xml.find(".//ar:Auth/ar:Token", ns).text, "TOKEN-DE-PRUEBA")

    def test_factura_c_sin_iva(self):
        total = Decimal("65000.00")
        self.solicitar(
            respuestas.CAE_A, cbte_tipo=11, neto=total, iva=Decimal("0.00"), alicuota_id=None
        )
        enviado = self.enviado()
        self.assertNotIn("<ar:Iva>", enviado)
        self.assertIn("<ar:ImpNeto>65000.00</ar:ImpNeto>", enviado)
        self.assertIn("<ar:ImpIVA>0.00</ar:ImpIVA>", enviado)

    def test_concepto_productos_no_lleva_fechas_de_servicio(self):
        self.solicitar(respuestas.CAE_A, concepto=1, servicio_desde=None, vto_pago=None)
        enviado = self.enviado()
        for nombre in ("FchServDesde", "FchServHasta", "FchVtoPago"):
            self.assertNotIn(nombre, enviado)

    def test_concepto_fuera_de_rango_no_envia(self):
        self.conexion(cuerpo=respuestas.CAE_A)
        for concepto in (0, 4):
            with self.subTest(concepto=concepto), self.assertRaises(ValueError):
                self.solicitar(concepto=concepto)
        self.con.request.assert_not_called()

    def test_nota_de_credito_lleva_la_factura_asociada(self):
        asociado = dict(
            tipo=6, punto_venta=3, numero=41, cuit="20000000001", fecha=date(2026, 10, 19)
        )
        self.solicitar(respuestas.CAE_A, cbte_tipo=8, asociado=asociado)
        xml = ET.fromstring(self.enviado())
        ns = {"ar": cliente.NS_WSFE}
        asoc = xml.find(".//ar:CbtesAsoc/ar:CbteAsoc", ns)
        self.assertEqual(
            [asoc[i].text for i in range(5)], ["6", "3", "41", "20000000001", "20261019"]
        )
        self.assertEqual(xml.find(".//ar:FeCabReq/ar:CbteTipo", ns).text, "8")

    def test_importe_exacto_a_2_decimales_pasa_y_con_3_o_float_no_sale(self):
        casos = [
            (Decimal("0.00"), True),  # cero
            (Decimal("0.01"), True),  # un centavo
            (Decimal("65000"), True),  # entero: se escribe 65000.00
            (Decimal("65000.005"), False),  # medio centavo: no se redondea en silencio
            (Decimal("0.001"), False),
            (65000.0, False),  # float
            (65000, False),  # int
        ]
        for monto, sale in casos:
            with self.subTest(monto=monto):
                self.conexion(cuerpo=respuestas.CAE_A)
                if sale:
                    self.solicitar(total=monto)
                    self.assertIn(f"<ar:ImpTotal>{monto:.2f}</ar:ImpTotal>", self.enviado())
                else:
                    with self.assertRaises(ValueError):
                        self.solicitar(total=monto)
                    self.con.request.assert_not_called()

    def test_token_con_caracteres_de_xml_sale_escapado(self):
        self.conexion(cuerpo=respuestas.CAE_A)
        cliente.solicitar_cae(self.emisor, {"token": "a<b&c", "sign": "s"}, pedido())
        self.assertIn("<ar:Token>a&lt;b&amp;c</ar:Token>", self.enviado())


class ConsultarTest(ConexionFalsaMixin, SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()

    def test_encontrado_da_los_datos_del_comprobante(self):
        self.conexion(cuerpo=respuestas.CONSULTA_ENCONTRADO)
        r = cliente.consultar(self.emisor, TA, 6, 42)
        self.assertEqual(r.tipo, "ok")
        self.assertEqual(
            r.datos,
            {
                "cae": "75123456789012",
                "cae_vence": date(2026, 10, 30),
                "fecha": date(2026, 10, 20),
                "doc_tipo": 96,
                "doc_numero": "30111222",
                "total": Decimal("65000.00"),
            },
        )
        enviado = self.enviado()
        for esperado in ("<ar:CbteTipo>6</ar:CbteTipo>", "<ar:CbteNro>42</ar:CbteNro>"):
            self.assertIn(esperado, enviado)

    def test_error_602_es_rechazado_porque_no_existe(self):
        self.conexion(cuerpo=respuestas.CONSULTA_NO_EXISTE)
        r = cliente.consultar(self.emisor, TA, 6, 42)
        self.assertEqual(r.tipo, "rechazado")
        self.assertIn("602", r.error)

    def test_otro_error_no_prueba_que_no_existe(self):
        # "No existe" habilita a emitir otro número: solo el 602 lo dice.
        self.conexion(cuerpo=respuestas.con_errores("FECompConsultar", "500", "Error interno"))
        self.assertEqual(cliente.consultar(self.emisor, TA, 6, 42).tipo, "reintentable")

    def test_error_de_token_es_reintentable_con_ticket_invalido(self):
        for codigo in ("600", "601"):
            with self.subTest(codigo=codigo):
                self.conexion(cuerpo=respuestas.con_errores("FECompConsultar", codigo, "token"))
                r = cliente.consultar(self.emisor, TA, 6, 42)
                self.assertEqual((r.tipo, r.datos), ("reintentable", {"token_invalido": True}))

    def test_respuesta_vacia_o_sin_cae_es_reintentable(self):
        vacia = respuestas.SOBRE.format(
            "<FECompConsultarResponse><FECompConsultarResult/></FECompConsultarResponse>"
        )
        sin_cae = respuestas.CONSULTA_ENCONTRADO.replace("75123456789012", "")
        for cuerpo in (vacia, sin_cae):
            self.conexion(cuerpo=cuerpo)
            self.assertEqual(cliente.consultar(self.emisor, TA, 6, 42).tipo, "reintentable")

    def test_timeout_es_reintentable(self):
        self.conexion(getresponse=TimeoutError())
        self.assertEqual(cliente.consultar(self.emisor, TA, 6, 42).tipo, "reintentable")


class FirmarTraTest(SistemaTestCase):
    def setUp(self):
        super().setUp()
        self.emisor = emisor_prueba()

    def firmar(self, visto=None):
        """Firma y anota en `visto` lo que `openssl` vio: carpeta y permisos de los PEM."""
        visto = {} if visto is None else visto
        real = cliente._openssl

        def espiar(*args, entrada):
            ruta = Path(args[args.index("-signer") + 1])
            visto["carpeta"] = ruta.parent
            visto["modos"] = {p.name: p.stat().st_mode & 0o777 for p in ruta.parent.iterdir()}
            return real(*args, entrada=entrada)

        with mock.patch.object(cliente, "_openssl", side_effect=espiar):
            return cliente.firmar_tra(self.emisor, ahora=AHORA), visto

    def test_el_cms_se_verifica_y_trae_el_tra_para_wsfe(self):
        firmado, _ = self.firmar()
        verif = subprocess.run(
            ["openssl", "cms", "-verify", "-inform", "DER", "-noverify"],
            input=base64.b64decode(firmado),
            capture_output=True,
            check=True,
        )
        tra = ET.fromstring(verif.stdout)
        self.assertEqual(tra.find("service").text, "wsfe")
        self.assertEqual(tra.find("header/uniqueId").text, str(int(AHORA.timestamp())))
        self.assertEqual(tra.find("header/generationTime").text, "2026-10-20T09:50:00-03:00")
        self.assertEqual(tra.find("header/expirationTime").text, "2026-10-20T10:10:00-03:00")

    def test_carpeta_temporal_con_permiso_0600_y_se_borra(self):
        _, visto = self.firmar()
        self.assertEqual(visto["modos"], {"cert.pem": 0o600, "clave.pem": 0o600})
        self.assertFalse(visto["carpeta"].exists())

    def test_la_carpeta_se_borra_aunque_openssl_falle(self):
        self.emisor.clave = "basura"
        visto = {}
        with self.assertRaises(ValueError):
            self.firmar(visto)
        self.assertFalse(visto["carpeta"].exists())

    def test_clave_rota_da_value_error_sin_pem_ni_traceback_con_clave(self):
        clave_buena = self.emisor.clave
        self.emisor.clave = "-----BEGIN PRIVATE KEY-----\nbasura\n-----END PRIVATE KEY-----"
        error = atrapar(ValueError, cliente.firmar_tra, self.emisor, ahora=AHORA)
        self.assertEqual(str(error), "No se pudo firmar el pedido a ARCA.")
        self.assertNotIn("BEGIN", str(error))
        self.assertEqual(fugas_en_traceback(error), [])
        self.assertNotEqual(self.emisor.clave, clave_buena)

    def test_certificado_vacio_da_value_error(self):
        self.emisor.certificado = ""
        with self.assertRaisesMessage(ValueError, "No se pudo firmar el pedido a ARCA."):
            cliente.firmar_tra(self.emisor, ahora=AHORA)

    def test_openssl_inexistente_da_value_error(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            error = atrapar(ValueError, cliente.firmar_tra, self.emisor, ahora=AHORA)
        self.assertEqual(fugas_en_traceback(error), [])
