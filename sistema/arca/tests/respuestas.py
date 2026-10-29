"""XML de ejemplo escritos a mano desde la documentación pública de WSAA y WSFEv1 (T037).

Sin datos reales: CAE, CUIT y documentos inventados.
"""

from xml.sax.saxutils import escape

SOBRE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<soap:Body>{}</soap:Body></soap:Envelope>"
)
WSFE = 'xmlns="http://ar.gov.afip.dif.FEV1/"'

TICKET = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><loginTicketResponse version="1.0">'
    "<header><source>CN=wsaahomo</source><uniqueId>1</uniqueId>"
    "<generationTime>2026-10-20T09:50:00.000-03:00</generationTime>"
    "<expirationTime>2026-10-20T21:50:00.000-03:00</expirationTime></header>"
    "<credentials><token>TOKEN-DE-PRUEBA</token><sign>SIGN-DE-PRUEBA</sign></credentials>"
    "</loginTicketResponse>"
)

LOGIN_OK = SOBRE.format(
    '<loginCmsResponse xmlns="http://wsaa.view.sua.dvadac.desein.afip.gov">'
    f"<loginCmsReturn>{escape(TICKET)}</loginCmsReturn></loginCmsResponse>"
)

LOGIN_TA_VALIDO = SOBRE.format(
    "<soap:Fault><faultcode>ns1:coe.alreadyAuthenticated</faultcode>"
    "<faultstring>El CEE ya posee un TA valido para el acceso al WSN solicitado</faultstring>"
    "</soap:Fault>"
)

ULTIMO_41 = SOBRE.format(
    f"<FECompUltimoAutorizadoResponse {WSFE}><FECompUltimoAutorizadoResult>"
    "<PtoVta>3</PtoVta><CbteTipo>6</CbteTipo><CbteNro>41</CbteNro>"
    "</FECompUltimoAutorizadoResult></FECompUltimoAutorizadoResponse>"
)


def con_errores(operacion: str, codigo: str, mensaje: str, *, cabecera: str = "") -> str:
    return SOBRE.format(
        f"<{operacion}Response {WSFE}><{operacion}Result>{cabecera}"
        f"<Errors><Err><Code>{codigo}</Code><Msg>{mensaje}</Msg></Err></Errors>"
        f"</{operacion}Result></{operacion}Response>"
    )


def cae(resultado: str, *, cae: str = "75123456789012", vence: str = "20261030", obs: str = ""):
    return SOBRE.format(
        f"<FECAESolicitarResponse {WSFE}><FECAESolicitarResult>"
        f"<FeCabResp><Cuit>20000000001</Cuit><PtoVta>3</PtoVta><CbteTipo>6</CbteTipo>"
        f"<FchProceso>20261020100000</FchProceso><CantReg>1</CantReg>"
        f"<Resultado>{resultado}</Resultado></FeCabResp>"
        f"<FeDetResp><FECAEDetResponse><Concepto>2</Concepto><DocTipo>96</DocTipo>"
        f"<DocNro>30111222</DocNro><CbteDesde>42</CbteDesde><CbteHasta>42</CbteHasta>"
        f"<CbteFch>20261020</CbteFch><Resultado>{resultado}</Resultado>"
        f"<CAE>{cae}</CAE><CAEFchVto>{vence}</CAEFchVto>{obs}"
        "</FECAEDetResponse></FeDetResp></FECAESolicitarResult></FECAESolicitarResponse>"
    )


CAE_A = cae("A")
CAE_R = cae(
    "R",
    cae="",
    vence="",
    obs="<Observaciones><Obs><Code>10015</Code><Msg>Doc. Nro. invalido</Msg></Obs></Observaciones>",
)
CAE_ERROR_600 = con_errores("FECAESolicitar", "600", "ValidacionDeToken: token invalido")
CAE_ERROR_10016 = con_errores("FECAESolicitar", "10016", "Numero de comprobante no es el proximo")

CONSULTA_ENCONTRADO = SOBRE.format(
    f"<FECompConsultarResponse {WSFE}><FECompConsultarResult><ResultGet>"
    "<Concepto>2</Concepto><DocTipo>96</DocTipo><DocNro>30111222</DocNro>"
    "<CbteDesde>42</CbteDesde><CbteHasta>42</CbteHasta><CbteFch>20261020</CbteFch>"
    "<ImpTotal>65000.00</ImpTotal><ImpNeto>53719.01</ImpNeto><ImpIVA>11280.99</ImpIVA>"
    "<Resultado>A</Resultado><CodAutorizacion>75123456789012</CodAutorizacion>"
    "<FchVto>20261030</FchVto></ResultGet></FECompConsultarResult></FECompConsultarResponse>"
)
CONSULTA_NO_EXISTE = con_errores("FECompConsultar", "602", "No existen datos en nuestros registros")
