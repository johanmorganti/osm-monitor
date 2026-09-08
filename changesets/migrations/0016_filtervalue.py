from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('changesets', '0015_changeset_upper_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='FilterValue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('field', models.CharField(choices=[('contributor', 'Contributor'), ('editor', 'Editor'), ('imagery', 'Imagery')], max_length=20)),
                ('value', models.CharField(max_length=255)),
            ],
            options={
                'unique_together': {('field', 'value')},
            },
        ),
    ]
