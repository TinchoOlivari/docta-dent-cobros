# Generated manually for custom permission on Payment

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('payment_instructions', '0015_auto_set_deposit_max_amount'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='payment',
            options={
                'ordering': ['-created_at'],
                'permissions': [
                    (
                        'view_all_deposit_payments_in_admin',
                        'Puede ver en el admin todas las señas (pagos tipo depósito), no solo las propias.',
                    ),
                ],
                'verbose_name': 'Pago',
                'verbose_name_plural': 'Pagos',
            },
        ),
    ]
