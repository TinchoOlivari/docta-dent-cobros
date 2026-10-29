"""Fase 1 del rediseño a sistema de ingresos.

Junta tres cosas que antes venían en 0020, 0021 y 0022: el rename total
especialista -> profesional y notas -> observaciones, el campo `metodo` (que abre
la puerta a efectivo y al resto de los medios de cobro), y el estado del pago
(PENDIENTE / COMPLETADO / CANCELADO) que reemplaza al modelo ReservaCupo.

Las tres migraciones viejas nunca llegaron a staging ni a producción — el último
número desplegado es 0019 — así que se colapsan en una sola en vez de arrastrar
un modelo que nunca existió fuera de una máquina de desarrollo.

Escrita a mano en vez de generada: el autodetector de Django no reconoce
specialist -> professional ni notes -> observaciones como renames (los atributos del
campo cambian junto con el nombre: verbose_name, help_text, related_name,
limit_choices_to), así que por default los propone como "borrar campo" + "agregar
campo", lo que pierde los datos existentes. Ver skill migraciones.

OJO AL DESPLEGAR: RenameField renombra la columna en el mismo momento en que corre.
deploy_prod.sh levanta el código nuevo ANTES de migrar, así que entre las dos cosas
hay unos segundos donde el código pide `professional_id` y la base todavía tiene
`specialist_id`: toda consulta de pagos devuelve 500. Es corto y conocido, pero el
deploy va fuera del horario de atención de la clínica.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def especialista_a_profesional(apps, schema_editor):
    User = apps.get_model("payment_instructions", "User")
    User.objects.filter(role="specialist").update(role="professional")


def profesional_a_especialista(apps, schema_editor):
    User = apps.get_model("payment_instructions", "User")
    User.objects.filter(role="professional").update(role="specialist")


class Migration(migrations.Migration):
    dependencies = [
        ("payment_instructions", "0019_alter_payment_options"),
    ]

    operations = [
        # --- rename especialista -> profesional ---
        migrations.RenameModel(
            old_name="SpecialistPaymentStats",
            new_name="ProfessionalPaymentStats",
        ),
        migrations.RenameField(
            model_name="payment",
            old_name="specialist",
            new_name="professional",
        ),
        migrations.AlterField(
            model_name="payment",
            name="professional",
            field=models.ForeignKey(
                help_text="Profesional asociado al pago",
                limit_choices_to={"role": "professional"},
                on_delete=django.db.models.deletion.PROTECT,
                related_name="professional_payments",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Profesional",
            ),
        ),
        # --- rename notas -> observaciones ---
        migrations.RenameField(
            model_name="payment",
            old_name="notes",
            new_name="observaciones",
        ),
        migrations.AlterField(
            model_name="payment",
            name="observaciones",
            field=models.TextField(
                blank=True,
                help_text="Observaciones adicionales sobre el pago",
                null=True,
                verbose_name="Observaciones",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("administrator", "Administrator"),
                    ("operator", "Operator"),
                    ("professional", "Professional"),
                ],
                default="operator",
                help_text="Rol del usuario",
                max_length=20,
            ),
        ),
        migrations.RunPython(especialista_a_profesional, profesional_a_especialista),
        # --- método de cobro ---
        migrations.AddField(
            model_name="payment",
            name="metodo",
            field=models.CharField(
                choices=[
                    ("TRANSFERENCIA", "Transferencia"),
                    ("EFECTIVO", "Efectivo"),
                    ("POSNET_QR", "Posnet QR"),
                    ("POSNET_TARJETA", "Posnet Tarjeta"),
                    ("LINK_PAGO", "Link de pago"),
                ],
                default="TRANSFERENCIA",
                help_text="Cómo se cobró: transferencia, efectivo, etc.",
                max_length=15,
                verbose_name="Método",
            ),
        ),
        # El efectivo no tiene cuenta receptora. Los pagos que ya existen son todos
        # transferencias y conservan su destinatario.
        migrations.AlterField(
            model_name="payment",
            name="payment_recipient",
            field=models.ForeignKey(
                blank=True,
                help_text="Destinatario del pago. Solo aplica a métodos con cuenta receptora.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payments",
                to="payment_instructions.paymentrecipient",
                verbose_name="Destinatario",
            ),
        ),
        # --- estado del pago ---
        # default=COMPLETADO deja todo el historial como cobrado, que es lo que es:
        # hasta hoy un pago no se podía registrar sin comprobante.
        migrations.AddField(
            model_name="payment",
            name="estado",
            field=models.CharField(
                choices=[
                    ("PENDIENTE", "Pendiente"),
                    ("COMPLETADO", "Completado"),
                    ("CANCELADO", "Cancelado"),
                ],
                default="COMPLETADO",
                help_text=(
                    "Pendiente: falta el comprobante, pero ya tomó cupo del destinatario. "
                    "Completado: cobrado. Cancelado: no se cobró y liberó el cupo."
                ),
                max_length=10,
                verbose_name="Estado",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="cancelado_at",
            field=models.DateTimeField(
                blank=True, editable=False, null=True, verbose_name="Cancelado el"
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="cancelado_por",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cancelled_payments",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Cancelado por",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="motivo_cancelacion",
            field=models.CharField(
                blank=True,
                choices=[
                    ("PACIENTE_NO_PAGO", "El paciente no pagó"),
                    ("COBRADO_OTRO_MEDIO", "Se cobró por otro medio"),
                    ("ERROR_DE_CARGA", "Error de carga"),
                    ("OTRO", "Otro"),
                ],
                max_length=20,
                null=True,
                verbose_name="Motivo de cancelación",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="observacion_cancelacion",
            field=models.TextField(
                blank=True,
                help_text='Obligatorio cuando el motivo es "Otro".',
                null=True,
                verbose_name="Detalle de la cancelación",
            ),
        ),
        # La lista de pendientes y los cálculos de cupo filtran por estado y fecha.
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(
                fields=["estado", "created_at"], name="payment_ins_estado_089efb_idx"
            ),
        ),
    ]
