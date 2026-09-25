from django.db import migrations, models


class Migration(migrations.Migration):
    # State-only: FilterValue.field has no DB-level CHECK constraint tied to
    # `choices` (plain CharField), so this doesn't touch the schema — just
    # keeps Django's migration history in sync with the FIELD_CHOICES
    # addition in models.py (AutocompleteView now accepts field=country too).

    dependencies = [
        ('changesets', '0051_add_cagg_editor_version_model'),
    ]

    operations = [
        migrations.AlterField(
            model_name='filtervalue',
            name='field',
            field=models.CharField(choices=[('contributor', 'Contributor'), ('editor', 'Editor'), ('imagery', 'Imagery'), ('country', 'Country')], max_length=20),
        ),
    ]
