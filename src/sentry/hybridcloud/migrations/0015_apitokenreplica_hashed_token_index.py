# Generated by Django 5.0.2 on 2024-03-14 10:40

from django.db import migrations, models

from sentry.new_migrations.migrations import CheckedMigration


class Migration(CheckedMigration):
    # This flag is used to mark that a migration shouldn't be automatically run in production. For
    # the most part, this should only be used for operations where it's safe to run the migration
    # after your code has deployed. So this should not be used for most operations that alter the
    # schema of a table.
    # Here are some things that make sense to mark as post deployment:
    # - Large data migrations. Typically we want these to be run manually by ops so that they can
    #   be monitored and not block the deploy for a long period of time while they run.
    # - Adding indexes to large tables. Since this can take a long time, we'd generally prefer to
    #   have ops run this and not block the deploy. Note that while adding an index is a schema
    #   change, it's completely safe to run the operation after the code has deployed.
    is_post_deployment = False

    dependencies = [
        ("hybridcloud", "0014_apitokenreplica_add_hashed_token"),
        ("sentry", "0675_dashboard_widget_query_rename_priority_sort_to_trends"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="apitokenreplica",
            index=models.Index(fields=["hashed_token"], name="hybridcloud_hashed__a93a8b_idx"),
        ),
    ]
