"""Carga datos inventados para probar el sistema (017/FR-034). No corre en producción."""

import io
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from PIL import Image

from sistema.arca.models import ComprobanteFiscal, Emisor
from sistema.cobros.models import Cobro, ItemCobro
from sistema.cobros.servicios import (
    adjuntar_comprobante,
    cancelar_cobro,
    crear_cobro,
    editar_cobro,
)
from sistema.destinatarios.models import Cuenta, Destinatario, Proveedor
from sistema.destinatarios.servicios import (
    crear_cuenta,
    crear_destinatario,
    crear_proveedor,
    editar_cuenta,
)
from sistema.terminales.models import Terminal
from sistema.terminales.servicios import abrir_sesion, crear_terminal
from sistema.usuarios import roles
from sistema.usuarios.servicios import crear_usuario

MONTOS = ["10000.00", "25000.50", "40000.00", "5000.01", "75000.00", "120000.00"]
ALIAS = ["ejemplo.uno", "ejemplo.dos", "ejemplo.tres"]
CUIT_EJEMPLO = "20000000001"
ALIAS_EXCEDENTE = "ejemplo.excedente"
OPERADOR = "ejemplo.operador"
CUIT_EMISOR = "20000000002"
# 018 fix: con 10 cobros o más, los cobros 5 a 9 muestran los otros 4 niveles del ícono de estado
# (verde, rojo por Dentalink, rojo por factura, gris); los Pendientes y el excedente dan el amarillo.
ESPECIALES = {4: "verde", 5: "error_envio", 6: "ajuste", 7: "factura_error", 8: "cancelado"}
MINIMO_ESPECIALES = 10


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, "PNG")
    return SimpleUploadedFile("comprobante-ejemplo.png", buffer.getvalue(), "image/png")


class Command(BaseCommand):
    help = "Crea destinatarios, cuentas y cobros de ejemplo con los servicios de cada app."

    def add_arguments(self, parser):
        parser.add_argument("--cobros", type=int, default=20, help="Cantidad de cobros (0 o más).")
        parser.add_argument(
            "--usuario",
            help="Usuario que carga los cobros (por defecto, ejemplo.operador).",
        )

    def handle(self, *args, **opciones):
        if settings.ENVIRONMENT == "production":
            raise CommandError("Este comando no corre en producción.")
        if opciones["cobros"] < 0:
            raise CommandError("--cobros debe ser 0 o más.")
        usuario = self._usuario(opciones["usuario"])
        admin = usuario if usuario.role == roles.ADMIN else self._admin()
        destinatarios = cuentas = 0
        if opciones["usuario"]:
            creador = usuario
        else:
            creador = self._operador(admin)
        if not Proveedor.objects.filter(nombre="Proveedor Ejemplo").exists():
            crear_proveedor(usuario=usuario, nombre="Proveedor Ejemplo")
        lista = []
        for n in (1, 2, 3):
            nombre = f"Destinatario Ejemplo {n}"
            dest = Destinatario.objects.filter(nombre=nombre).first()
            if dest is None:
                dest = crear_destinatario(usuario=usuario, nombre=nombre, tipo="profesional")
                destinatarios += 1
            lista.append(dest)
            if not Cuenta.objects.filter(alias__iexact=ALIAS[n - 1]).exists():
                crear_cuenta(
                    usuario=usuario,
                    tipo="transferencia",
                    destinatario=dest,
                    alias=ALIAS[n - 1],
                    cbu=f"{n:022d}",
                    titular=f"Titular Ejemplo {n}",
                    titular_cuit=CUIT_EJEMPLO if n == 1 else None,
                    tope_mensual=Decimal("1000000.00"),
                    tope_semanal=Decimal("0.00"),
                    minimo_por_pago=Decimal("0.00"),
                )
                cuentas += 1
            elif n == 1:
                uno = Cuenta.objects.get(alias__iexact=ALIAS[0])
                if uno.titular_cuit != CUIT_EJEMPLO:
                    editar_cuenta(uno, usuario=usuario, titular_cuit=CUIT_EJEMPLO)
        if not Cuenta.objects.habilitadas().filter(tipo="efectivo").exists():
            crear_cuenta(usuario=usuario, tipo="efectivo", destinatario=lista[0])
            cuentas += 1
        excedente = Cuenta.objects.filter(alias__iexact=ALIAS_EXCEDENTE).first()
        if excedente is None:
            excedente = crear_cuenta(
                usuario=usuario,
                tipo="transferencia",
                destinatario=lista[2],
                alias=ALIAS_EXCEDENTE,
                cbu=f"{4:022d}",
                titular="Titular Ejemplo 4",
                tope_mensual=Decimal("10000.00"),
            )
            cuentas += 1
        cuentas += self._terminal_pos(usuario, lista[0])
        cobros = sin_cuenta = 0
        for i in range(opciones["cobros"]):
            monto = Decimal(MONTOS[i % len(MONTOS)])
            tipo = i % 3  # 0 Pendiente, 1 Finalizado con comprobante, 2 efectivo
            especial = ESPECIALES.get(i) if opciones["cobros"] >= MINIMO_ESPECIALES else None
            if especial:
                tipo = 1
            con_excedente = i == 3 and admin is not None  # un Pendiente que pasa el tope
            if con_excedente:
                monto = Decimal("5000.00")
            try:
                cobro = crear_cobro(
                    usuario=creador,
                    metodo="efectivo" if tipo == 2 else "transferencia",
                    monto=monto,
                    nombre_paciente=f"Paciente Ejemplo {i + 1}",
                )
                if tipo == 1:
                    adjuntar_comprobante(cobro, _png(), usuario=creador, estado_visto=cobro.estado)
                if especial:
                    self._especial(cobro, especial, i, admin)
                if con_excedente:
                    # 007/FR-041: entra en la cuenta con cupo y sube después a $15.000,00
                    for datos in ({"cuenta": excedente}, {"monto": Decimal("15000.00")}):
                        cobro = editar_cobro(
                            cobro, usuario=admin, estado_visto="pendiente", **datos
                        )
            except ValidationError as e:
                self.stderr.write(f"Cobro {i + 1} no creado: {' '.join(e.messages)}")
                sin_cuenta += 1
                continue
            cobros += 1
        self.stdout.write(
            f"Destinatarios: {destinatarios}. Cuentas: {cuentas}. Cobros: {cobros}. "
            f"Sin cuenta disponible: {sin_cuenta}."
        )

    def _especial(self, cobro, caso, i, admin):
        """Deja el cobro en el estado de `caso`. Datos inventados, solo fuera de producción.

        Límite: los servicios públicos de ítems y facturas exigen Dentalink y ARCA reales, así que
        los ítems (estado de envío) y la factura con error se insertan directo en la base.
        """
        if caso == "cancelado":
            cobro.refresh_from_db()  # el comprobante lo pasó a Finalizado
            cancelar_cobro(cobro, usuario=admin, motivo="otro_medio", estado_visto=cobro.estado)
            return
        Cobro.objects.filter(pk=cobro.pk).update(faltan_items=False)
        Envio = ItemCobro.EstadoEnvio
        estado_envio = {"error_envio": Envio.ERROR, "ajuste": Envio.REQUIERE_AJUSTE}.get(
            caso, Envio.ENVIADO
        )
        ItemCobro.objects.create(
            cobro=cobro,
            numero=1,
            id_prestacion=900000 + i,
            nombre_tratamiento="Tratamiento Ejemplo",
            nombre_prestacion="Prestación Ejemplo",
            nombre_profesional="Profesional Ejemplo",
            monto=cobro.monto,
            estado_envio=estado_envio,
            ultimo_error="" if estado_envio == Envio.ENVIADO else "Error inventado de ejemplo.",
        )
        if caso == "factura_error":
            emisor, _ = Emisor.objects.get_or_create(
                cuit=CUIT_EMISOR,
                defaults={
                    "razon_social": "Emisor Ejemplo",
                    "condicion_iva": Emisor.CondicionIva.MONOTRIBUTO,
                    "punto_venta": 1,
                    "tipo_comprobante": Emisor.TipoComprobante.C,
                    "alicuota_iva": Emisor.Alicuota.CERO,
                    "estado": Emisor.Estado.INACTIVO,  # inactivo: no emite facturas en corridas futuras
                },
            )
            ComprobanteFiscal.objects.create(
                cobro=cobro,
                emisor=emisor,
                tipo=ComprobanteFiscal.Tipo.FACTURA,
                estado=ComprobanteFiscal.Estado.ERROR,
                concepto=Emisor.Concepto.SERVICIOS,
                descripcion="Servicios odontológicos",
                motivo="Error inventado de ejemplo.",
            )

    def _terminal_pos(self, usuario, destinatario):
        """1 terminal POS con su cuenta y la sesión abierta. Devuelve las cuentas creadas."""
        if Terminal.objects.filter(nombre="Terminal Ejemplo").exists():
            return 0
        proveedor = Proveedor.objects.get(nombre="Proveedor Ejemplo")
        terminal = crear_terminal(
            usuario=usuario,
            nombre="Terminal Ejemplo",
            proveedor=proveedor,
            acepta_credito=True,
            acepta_debito=True,
        )
        cuenta = crear_cuenta(
            usuario=usuario,
            tipo="terminal_pos",
            destinatario=destinatario,
            proveedor=proveedor,
            titular="Cuenta POS Ejemplo",
            objetivo=Decimal("1000000.00"),
        )
        abrir_sesion(terminal, cuenta, timezone.now() - timedelta(minutes=1), usuario=usuario)
        return 1

    def _admin(self):
        User = get_user_model()
        return User.objects.filter(role=roles.ADMIN, is_active=True).order_by("pk").first()

    def _operador(self, admin):
        User = get_user_model()
        operador = User.objects.filter(username=OPERADOR).first()
        if operador is None:
            clave = secrets.token_urlsafe(12)
            operador = crear_usuario(
                usuario=admin, username=OPERADOR, password=clave, role=roles.OPERATOR
            )
            self.stdout.write(f"Operador {OPERADOR} creado. Contraseña: {clave}")
        return operador

    def _usuario(self, username):
        User = get_user_model()
        if username:
            usuario = User.objects.filter(username=username, is_active=True).first()
            if usuario is None:
                raise CommandError(f"No hay un usuario activo «{username}».")
            return usuario
        usuario = User.objects.filter(role=roles.ADMIN, is_active=True).order_by("pk").first()
        if usuario is None:
            raise CommandError("No hay un administrador activo. Usá --usuario.")
        return usuario
