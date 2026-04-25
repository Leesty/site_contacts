# Generated manually on 2026-04-26

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0055_phone_callback_reports'),
    ]

    operations = [
        migrations.CreateModel(
            name='WithdrawalReceipt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file', models.FileField(upload_to='receipts/', help_text='Файл чека (скриншот).')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True, help_text='Когда загружен этот чек.')),
                ('note', models.CharField(blank=True, default='', max_length=255, help_text='Опционально: пометка к чеку (например, «частичная оплата 1/2»).')),
                ('withdrawal_request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='receipts', to='core.withdrawalrequest')),
            ],
            options={
                'verbose_name': 'Чек выплаты',
                'verbose_name_plural': 'Чеки выплат',
                'ordering': ['uploaded_at'],
            },
        ),
    ]
