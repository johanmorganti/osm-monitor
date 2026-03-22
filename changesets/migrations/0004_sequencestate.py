from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changesets', '0003_changeset_created_by_family_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='SequenceState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_sequence', models.IntegerField()),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'app_label': 'changesets',
            },
        ),
    ]
