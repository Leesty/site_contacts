# Generated manually on 2026-04-25

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0054_zvonok_settings'),
    ]

    operations = [
        # --- SearchReport: новый тип + поля для phone_callback ---
        migrations.AlterField(
            model_name='searchreport',
            name='status',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('pending', 'На проверке'),
                    ('approved', 'Одобрен'),
                    ('rejected', 'Отклонён'),
                    ('rework', 'На доработке'),
                    ('pending_callback', 'Ждём нажатия 1'),
                ],
                default='pending',
                db_index=True,
            ),
        ),
        migrations.AddField(
            model_name='searchreport',
            name='report_type',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('bot_start', 'Бот запущен'),
                    ('phone_callback', 'Номер для созвона'),
                ],
                default='bot_start',
                db_index=True,
                help_text='Тип отчёта: bot_start — клиент запустил бота (150₽); phone_callback — клиент оставил номер, робот прозванивает (65₽).',
            ),
        ),
        migrations.AddField(
            model_name='searchreport',
            name='client_phone',
            field=models.CharField(max_length=32, blank=True, default='', help_text='Номер клиента в формате +7XXXXXXXXXX (только для phone_callback).'),
        ),
        migrations.AddField(
            model_name='searchreport',
            name='callback_at',
            field=models.DateTimeField(null=True, blank=True, help_text='Дата/время желаемого созвона с клиентом (только для phone_callback).'),
        ),
        migrations.AddField(
            model_name='searchreport',
            name='callback_confirmed_at',
            field=models.DateTimeField(null=True, blank=True, help_text='Когда клиент впервые нажал 1 на любом из звонков.'),
        ),
        # --- SiteSettings: 3 кампании + секрет webhook ---
        migrations.AddField(
            model_name='sitesettings',
            name='zvonok_campaign_id_now',
            field=models.CharField(max_length=64, blank=True, default='', help_text='Campaign ID zvonok.com для стадии 1 — звонок сразу после подачи отчёта.'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='zvonok_campaign_id_1h',
            field=models.CharField(max_length=64, blank=True, default='', help_text='Campaign ID для стадии 2 — звонок за 1 час до времени созвона.'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='zvonok_campaign_id_10min',
            field=models.CharField(max_length=64, blank=True, default='', help_text='Campaign ID для стадии 3 — звонок за 10 минут до времени созвона.'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='zvonok_webhook_secret',
            field=models.CharField(max_length=64, blank=True, default='', help_text="Секрет для проверки webhook'ов от zvonok.com (используется в URL callback'а)."),
        ),
        # --- RobocallAttempt: новая модель ---
        migrations.CreateModel(
            name='RobocallAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('stage', models.IntegerField(choices=[(1, 'Сразу'), (2, 'За 1 час'), (3, 'За 10 минут')])),
                ('zvonok_campaign_id', models.CharField(max_length=64, blank=True, default='', help_text='ID кампании zvonok.com, из которой запускался звонок.')),
                ('scheduled_at', models.DateTimeField(db_index=True, help_text='Когда по плану должен улететь запрос в zvonok (UTC).')),
                ('fired_at', models.DateTimeField(null=True, blank=True, help_text='Когда реально ушёл POST к zvonok API.')),
                ('skipped', models.BooleanField(default=False, help_text='Пропущен (например, scheduled_at ушёл в прошлое > grace period до запуска планировщика).')),
                ('skip_reason', models.CharField(max_length=255, blank=True, default='')),
                ('zvonok_call_id', models.CharField(max_length=64, blank=True, default='', db_index=True, help_text='call_id от zvonok (используется для матчинга webhook callback).')),
                ('zvonok_response', models.TextField(blank=True, default='', help_text='Последний сырой ответ от zvonok API (для диагностики).')),
                ('button_pressed', models.BooleanField(default=False, db_index=True, help_text='Клиент нажал 1 по этому звонку (из webhook).')),
                ('button_pressed_at', models.DateTimeField(null=True, blank=True)),
                ('dial_status', models.CharField(max_length=64, blank=True, default='', help_text='Статус набора из webhook (answered/no_answer/busy/...).')),
                ('webhook_received_at', models.DateTimeField(null=True, blank=True)),
                ('search_report', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='robocall_attempts', to='core.searchreport')),
            ],
            options={
                'verbose_name': 'Звонок робота',
                'verbose_name_plural': 'Звонки робота',
                'ordering': ['search_report_id', 'stage'],
                'unique_together': {('search_report', 'stage')},
            },
        ),
    ]
