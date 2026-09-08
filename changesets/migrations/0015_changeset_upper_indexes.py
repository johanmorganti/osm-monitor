from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    # CONCURRENTLY builds can't run inside a transaction.
    atomic = False

    dependencies = [
        ('changesets', '0014_alter_changeset_locale'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='changeset',
            index=models.Index(Upper('user'), name='changeset_user_upper_idx'),
        ),
        AddIndexConcurrently(
            model_name='changeset',
            index=models.Index(Upper('created_by_family'), name='changeset_editor_upper_idx'),
        ),
        AddIndexConcurrently(
            model_name='changeset',
            index=models.Index(Upper('imagery_family'), name='changeset_imagery_upper_idx'),
        ),
    ]
