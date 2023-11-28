from typing import Tuple
from unittest.mock import patch

import pytest
from django.apps import apps
from django.db.models import Max, QuerySet

from sentry.backup.scopes import RelocationScope
from sentry.db.models import Model, region_silo_only_model
from sentry.db.models.fields.hybrid_cloud_foreign_key import HybridCloudForeignKey
from sentry.discover.models import DiscoverSavedQuery
from sentry.models.integrations.external_issue import ExternalIssue
from sentry.models.integrations.integration import Integration
from sentry.models.outbox import ControlOutbox, OutboxScope, outbox_context
from sentry.models.savedsearch import SavedSearch
from sentry.models.user import User
from sentry.silo import SiloMode
from sentry.tasks.deletion.hybrid_cloud import (
    get_watermark,
    schedule_hybrid_cloud_foreign_key_jobs,
    schedule_hybrid_cloud_foreign_key_jobs_control,
    set_watermark,
)
from sentry.testutils.factories import Factories
from sentry.testutils.helpers.task_runner import BurstTaskRunner
from sentry.testutils.outbox import outbox_runner
from sentry.testutils.pytest.fixtures import django_db_all
from sentry.testutils.silo import assume_test_silo_mode, control_silo_test, region_silo_test
from sentry.types.region import find_regions_for_user


@region_silo_only_model
class DoNothingIntegrationModel(Model):
    __relocation_scope__ = RelocationScope.Excluded
    integration_id = HybridCloudForeignKey("sentry.Integration", on_delete="DO_NOTHING")

    class Meta:
        app_label = "fixtures"


@pytest.fixture(autouse=True)
def batch_size_one():
    with patch("sentry.deletions.base.ModelDeletionTask.DEFAULT_QUERY_LIMIT", new=1), patch(
        "sentry.tasks.deletion.hybrid_cloud.get_batch_size", return_value=1
    ):
        yield


def reset_watermarks():
    """
    Reset watermarks to simulate that we are 'caught up'
    Without this, the records generated by tests elsewhere in CI
    result in the delta between max(id) and 0 is too wide. Because
    we also mock the batch size to 1 in this module we run out of stack
    frames spawning celery jobs inside of each other (which are run immediately).
    """
    silo_mode = SiloMode.get_current_mode()
    for app_models in apps.all_models.values():
        for model in app_models.values():
            if not hasattr(model._meta, "silo_limit"):
                continue
            if silo_mode not in model._meta.silo_limit.modes:
                continue
            for field in model._meta.fields:
                if not isinstance(field, HybridCloudForeignKey):
                    continue
                max_val = model.objects.aggregate(Max("id"))["id__max"] or 0
                set_watermark("tombstone", field, max_val, "abc123")
                set_watermark("row", field, max_val, "abc123")


@pytest.fixture
def saved_search_owner_id_field():
    return SavedSearch._meta.get_field("owner_id")


@django_db_all
@region_silo_test
def test_no_work_is_no_op(task_runner, saved_search_owner_id_field):
    reset_watermarks()

    # Transaction id should not change when no processing occurs.  (this would happen if setting the next cursor
    # to the same, previous value.)
    level, tid = get_watermark("tombstone", saved_search_owner_id_field)
    assert level == 0

    with task_runner():
        schedule_hybrid_cloud_foreign_key_jobs()

    assert get_watermark("tombstone", saved_search_owner_id_field) == (0, tid)


@django_db_all
def test_watermark_and_transaction_id(task_runner, saved_search_owner_id_field):
    _, tid1 = get_watermark("tombstone", saved_search_owner_id_field)
    # TODO: Add another test to validate the tid is unique per field

    _, tid2 = get_watermark("row", saved_search_owner_id_field)

    assert tid1
    assert tid2
    assert tid1 != tid2

    set_watermark("tombstone", saved_search_owner_id_field, 5, tid1)
    wm, new_tid1 = get_watermark("tombstone", saved_search_owner_id_field)

    assert new_tid1 != tid1
    assert wm == 5

    assert get_watermark("tombstone", saved_search_owner_id_field) == (wm, new_tid1)


@assume_test_silo_mode(SiloMode.MONOLITH)
def setup_deletable_objects(
    count=1, send_tombstones=True, u_id=None
) -> Tuple[QuerySet, ControlOutbox]:
    if u_id is None:
        u = Factories.create_user()
        u_id = u.id
        with outbox_context(flush=False):
            u.delete()

    for i in range(count):
        Factories.create_saved_search(f"s-{i}", owner_id=u_id)

    for region_name in find_regions_for_user(u_id):
        shard = ControlOutbox(
            shard_scope=OutboxScope.USER_SCOPE, shard_identifier=u_id, region_name=region_name
        )
        if send_tombstones:
            shard.drain_shard()

        return SavedSearch.objects.filter(owner_id=u_id), shard
    assert False, "find_regions_for_user could not determine a region for production."


@django_db_all
@region_silo_test
def test_region_processing(task_runner):
    reset_watermarks()

    # Assume we have two groups of objects
    # Both of them have been deleted, but only the first set has their tombstones sent yet.
    results1, shard1 = setup_deletable_objects(10)
    results2, shard2 = setup_deletable_objects(10, send_tombstones=False)

    # Test validation
    assert results1.exists()
    assert results2.exists()

    # Processing now only removes the first set
    with BurstTaskRunner() as burst:
        schedule_hybrid_cloud_foreign_key_jobs()

    burst()

    assert not results1.exists()
    assert results2.exists()

    # Processing after the tombstones arrives, still converges later.
    with assume_test_silo_mode(SiloMode.MONOLITH):
        shard2.drain_shard()
    with task_runner():
        schedule_hybrid_cloud_foreign_key_jobs()
    assert not results2.exists()

    # Processing for a new record created after its tombstone is processed, still converges.
    results3, shard3 = setup_deletable_objects(10, u_id=shard1.object_identifier)
    assert results3.exists()
    with task_runner():
        schedule_hybrid_cloud_foreign_key_jobs()
    assert not results3.exists()


@django_db_all
@control_silo_test
def test_control_processing(task_runner):
    reset_watermarks()

    with assume_test_silo_mode(SiloMode.CONTROL):
        results, _ = setup_deletable_objects(10)
        with BurstTaskRunner() as burst:
            schedule_hybrid_cloud_foreign_key_jobs_control()

        burst()

        # Do not process
        assert results.exists()


def setup_deletion_test():
    user = Factories.create_user()
    organization = Factories.create_organization(region="eu", owner=user)
    project = Factories.create_project(organization=organization)
    integration = Factories.create_integration(organization=organization, external_id="123")
    group = Factories.create_group(project=project)
    external_issue = Factories.create_integration_external_issue(
        group=group, integration=integration, key="abc123"
    )
    saved_query = DiscoverSavedQuery.objects.create(
        name="disco-query",
        organization=organization,
        created_by_id=user.id,
    )
    return {
        "user": user,
        "organization": organization,
        "project": project,
        "integration": integration,
        "group": group,
        "external_issue": external_issue,
        "saved_query": saved_query,
    }


@django_db_all
@region_silo_test
def test_cascade_deletion_behavior(task_runner):
    data = setup_deletion_test()
    integration = data["integration"]
    external_issue = data["external_issue"]

    integration_id = integration.id
    with assume_test_silo_mode(SiloMode.CONTROL), outbox_runner():
        integration.delete()

        assert not Integration.objects.filter(id=integration_id).exists()

    with BurstTaskRunner() as burst:
        schedule_hybrid_cloud_foreign_key_jobs()

    burst()

    # Deletion cascaded
    assert not ExternalIssue.objects.filter(id=external_issue.id).exists()


@django_db_all
@region_silo_test
def test_do_nothing_deletion_behavior(task_runner):
    data = setup_deletion_test()
    integration = data["integration"]

    integration_id = integration.id
    model = DoNothingIntegrationModel.objects.create(integration_id=integration_id)

    with assume_test_silo_mode(SiloMode.CONTROL), outbox_runner():
        integration.delete()

        assert not Integration.objects.filter(id=integration_id).exists()

    with BurstTaskRunner() as burst:
        schedule_hybrid_cloud_foreign_key_jobs()

    burst()

    # Deletion did nothing
    model = DoNothingIntegrationModel.objects.get(id=model.id)
    assert model.integration_id == integration_id


@django_db_all
@region_silo_test
def test_set_null_deletion_behavior(task_runner):
    data = setup_deletion_test()
    user = data["user"]
    saved_query = data["saved_query"]

    user_id = user.id
    with assume_test_silo_mode(SiloMode.CONTROL), outbox_runner():
        user.delete()

        assert not User.objects.filter(id=user_id).exists()

    with BurstTaskRunner() as burst:
        schedule_hybrid_cloud_foreign_key_jobs()

    burst()

    # Deletion set field to null
    saved_query = DiscoverSavedQuery.objects.get(id=saved_query.id)
    assert saved_query.created_by_id is None
