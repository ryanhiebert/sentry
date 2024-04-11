# Generated by Django 3.2.20 on 2023-09-20 00:47

from datetime import timedelta

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import F

from sentry.new_migrations.migrations import CheckedMigration
from sentry.utils.query import RangeQuerySetWrapperWithProgressBar


def fix_next_checkin_latest(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    MonitorEnvironment = apps.get_model("sentry", "MonitorEnvironment")
    query = MonitorEnvironment.objects.select_related("monitor").filter(
        next_checkin=F("next_checkin_latest")
    )

    for monitor_env in RangeQuerySetWrapperWithProgressBar(query):
        margin = monitor_env.monitor.config.get("checkin_margin") or 1

        # Update the next_checkin_latest to match their configured margin
        monitor_env.next_checkin_latest = monitor_env.next_checkin + timedelta(minutes=margin)
        monitor_env.save()


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
        ("sentry", "0567_add_slug_reservation_model"),
    ]

    operations = [
        migrations.RunPython(
            fix_next_checkin_latest,
            migrations.RunPython.noop,
            hints={"tables": ["sentry_monitor"]},
        ),
    ]
