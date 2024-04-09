# Generated by Django 3.2.20 on 2023-08-29 19:04

from django.db import migrations

from sentry.new_migrations.migrations import CheckedMigration
from sentry.utils.query import RangeQuerySetWrapperWithProgressBar


def remove_name_data(apps, schema_editor):
    Rule = apps.get_model("sentry", "Rule")

    for rule in RangeQuerySetWrapperWithProgressBar(Rule.objects.all()):
        for action in rule.data.get("actions", []):
            if action.get("name") or action.get("name") in [0, ""]:
                del action["name"]

        for condition in rule.data.get("conditions", []):
            if condition.get("name") or condition.get("name") in [0, ""]:
                del condition["name"]

        rule.save(update_fields=["data"])


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
    is_post_deployment = True

    dependencies = [
        ("sentry", "0537_backfill_xactor_team_and_user_ids"),
    ]

    operations = [
        migrations.RunPython(
            remove_name_data,
            migrations.RunPython.noop,
            hints={"tables": ["sentry_rule"]},
        ),
    ]
