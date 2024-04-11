# Generated by Django 3.2.20 on 2023-09-14 03:21

import django.utils.timezone
from django.db import migrations, models

import sentry.db.models.fields.bounded
import sentry.db.models.fields.hybrid_cloud_foreign_key
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
        ("sentry", "0553_add_new_index_to_groupedmessage_table"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeamReplica",
            fields=[
                (
                    "id",
                    sentry.db.models.fields.bounded.BoundedBigAutoField(
                        primary_key=True, serialize=False
                    ),
                ),
                (
                    "team_id",
                    sentry.db.models.fields.hybrid_cloud_foreign_key.HybridCloudForeignKey(
                        "sentry.Team", db_index=True, on_delete="CASCADE"
                    ),
                ),
                (
                    "organization_id",
                    sentry.db.models.fields.hybrid_cloud_foreign_key.HybridCloudForeignKey(
                        "sentry.Organization", db_index=True, on_delete="CASCADE"
                    ),
                ),
                ("slug", models.SlugField()),
                ("name", models.CharField(max_length=64)),
                ("status", sentry.db.models.fields.bounded.BoundedPositiveIntegerField()),
                ("date_added", models.DateTimeField(default=django.utils.timezone.now)),
                ("org_role", models.CharField(max_length=32, null=True)),
            ],
            options={
                "db_table": "sentry_teamreplica",
                "unique_together": {("organization_id", "slug")},
            },
        ),
    ]
