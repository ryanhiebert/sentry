from __future__ import annotations

import functools
import logging
import os
import re
import time
from collections import namedtuple
from collections.abc import Callable, Collection, Mapping, MutableMapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from typing import Any, Union
from urllib.parse import urlparse

import sentry_sdk
import urllib3
from dateutil.parser import parse as parse_datetime
from django.conf import settings
from django.core.cache import cache
from sentry_sdk import Hub
from snuba_sdk import MetricsQuery, Request
from snuba_sdk.legacy import json_to_snql

from sentry.models.environment import Environment
from sentry.models.group import Group
from sentry.models.grouprelease import GroupRelease
from sentry.models.organization import Organization
from sentry.models.project import Project
from sentry.models.projectkey import ProjectKey
from sentry.models.release import Release
from sentry.models.releases.release_project import ReleaseProject
from sentry.net.http import connection_from_url
from sentry.snuba.dataset import Dataset
from sentry.snuba.events import Columns
from sentry.snuba.referrer import validate_referrer
from sentry.utils import json, metrics
from sentry.utils.dates import outside_retention_with_modified_start

logger = logging.getLogger(__name__)

# Sentinels
ROUND_UP = object()
ROUND_DOWN = object()

# We limit the number of fields an user can ask for
# in a single query to lessen the load on snuba
MAX_FIELDS = 20

SAFE_FUNCTIONS = frozenset(["NOT IN"])
SAFE_FUNCTION_RE = re.compile(r"-?[a-zA-Z_][a-zA-Z0-9_]*$")
# Match any text surrounded by quotes, can't use `.*` here since it
# doesn't include new lines,
QUOTED_LITERAL_RE = re.compile(r"^'[\s\S]*'$")

MEASUREMENTS_KEY_RE = re.compile(r"^measurements\.([a-zA-Z0-9-_.]+)$")
# Matches span op breakdown field
SPAN_OP_BREAKDOWNS_FIELD_RE = re.compile(r"^spans\.([a-zA-Z0-9-_.]+)$")
# Matches span op breakdown snuba key
SPAN_OP_BREAKDOWNS_KEY_RE = re.compile(r"^ops\.([a-zA-Z0-9-_.]+)$")

# Global Snuba request option override dictionary. Only intended
# to be used with the `options_override` contextmanager below.
# NOT THREAD SAFE!
OVERRIDE_OPTIONS = {
    "consistent": os.environ.get("SENTRY_SNUBA_CONSISTENT", "false").lower() in ("true", "1")
}

# Show the snuba query params and the corresponding sql or errors in the server logs
SNUBA_INFO_FILE = os.environ.get("SENTRY_SNUBA_INFO_FILE", "")


def log_snuba_info(content):
    if SNUBA_INFO_FILE:
        with open(SNUBA_INFO_FILE, "a") as file:
            file.writelines(content)
    else:
        print(content)  # NOQA: only prints when an env variable is set


SNUBA_INFO = (
    os.environ.get("SENTRY_SNUBA_INFO", "false").lower() in ("true", "1") or SNUBA_INFO_FILE
)

if SNUBA_INFO:
    import sqlparse

# There are several cases here where we support both a top level column name and
# a tag with the same name. Existing search patterns expect to refer to the tag,
# so we support <real_column_name>.name to refer to the top level column name.
SENTRY_SNUBA_MAP = {
    col.value.alias: col.value.event_name for col in Columns if col.value.event_name is not None
}

TRANSACTIONS_SNUBA_MAP = {
    col.value.alias: col.value.transaction_name
    for col in Columns
    if col.value.transaction_name is not None
}

ISSUE_PLATFORM_MAP = {
    col.value.alias: col.value.issue_platform_name
    for col in Columns
    if col.value.issue_platform_name is not None
}

SPAN_COLUMN_MAP = {
    # These are deprecated, keeping them for now while we migrate the frontend
    "action": "action",
    "description": "description",
    "domain": "domain",
    "group": "group",
    "module": "module",
    "id": "span_id",
    "parent_span": "parent_span_id",
    "platform": "platform",
    "project": "project_id",
    "span.action": "action",
    "span.description": "description",
    "span.domain": "domain",
    # DO NOT directly expose span.duration, we should always use the alias
    # "span.duration": "duration",
    "span.group": "group",
    "span.module": "module",
    "span.op": "op",
    "span.self_time": "exclusive_time",
    "span.status": "span_status",
    "timestamp": "timestamp",
    "trace": "trace_id",
    "transaction": "segment_name",
    "transaction.id": "transaction_id",
    "segment.id": "segment_id",
    "transaction.op": "transaction_op",
    "user": "user",
    "profile_id": "profile_id",  # deprecated in favour of `profile.id`
    "profile.id": "profile_id",
    "cache.hit": "sentry_tags[cache.hit]",
    "transaction.method": "sentry_tags[transaction.method]",
    "system": "sentry_tags[system]",
    "raw_domain": "sentry_tags[raw_domain]",
    "release": "sentry_tags[release]",
    "environment": "sentry_tags[environment]",
    "device.class": "sentry_tags[device.class]",
    "category": "sentry_tags[category]",
    "span.category": "sentry_tags[category]",
    "span.status_code": "sentry_tags[status_code]",
    "resource.render_blocking_status": "sentry_tags[resource.render_blocking_status]",
    "http.response_content_length": "sentry_tags[http.response_content_length]",
    "http.decoded_response_content_length": "sentry_tags[http.decoded_response_content_length]",
    "http.response_transfer_size": "sentry_tags[http.response_transfer_size]",
    "app_start_type": "sentry_tags[app_start_type]",
    "replay.id": "sentry_tags[replay_id]",
    "browser.name": "sentry_tags[browser.name]",
    "origin.transaction": "sentry_tags[transaction]",
    "is_transaction": "is_segment",
}

METRICS_SUMMARIES_COLUMN_MAP = {
    "project": "project_id",
    "id": "span_id",
    "trace": "trace_id",
    "metric": "metric_mri",
    "timestamp": "end_timestamp",
    "segment.id": "segment_id",
    "span.duration": "duration_ms",
    "span.group": "group",
    "min_metric": "min",
    "max_metric": "max",
    "sum_metric": "sum",
    "count_metric": "count",
}

SPAN_COLUMN_MAP.update(
    {col.value.alias: col.value.spans_name for col in Columns if col.value.spans_name is not None}
)

SESSIONS_FIELD_LIST = [
    "release",
    "sessions",
    "sessions_crashed",
    "users",
    "users_crashed",
    "project_id",
    "org_id",
    "environment",
    "session.status",
    "users_errored",
    "users_abnormal",
    "sessions_errored",
    "sessions_abnormal",
    "duration_quantiles",
    "duration_avg",
]

SESSIONS_SNUBA_MAP = {column: column for column in SESSIONS_FIELD_LIST}
SESSIONS_SNUBA_MAP.update({"timestamp": "started"})

# This maps the public column aliases to the discover dataset column names.
# Longer term we would like to not expose the transactions dataset directly
# to end users and instead have all ad-hoc queries go through the discover
# dataset.
DISCOVER_COLUMN_MAP = {
    col.value.alias: col.value.discover_name
    for col in Columns
    if col.value.discover_name is not None
}

# Not using the Columns enum here, because there are far fewer columns in the metrics tables
METRICS_COLUMN_MAP = {
    "timestamp": "timestamp",
    "project_id": "project_id",
    "project.id": "project_id",
    "organization_id": "org_id",
}


DATASETS: dict[Dataset, dict[str, str]] = {
    Dataset.Events: SENTRY_SNUBA_MAP,
    Dataset.Transactions: TRANSACTIONS_SNUBA_MAP,
    Dataset.Discover: DISCOVER_COLUMN_MAP,
    Dataset.Sessions: SESSIONS_SNUBA_MAP,
    Dataset.Metrics: METRICS_COLUMN_MAP,
    Dataset.MetricsSummaries: METRICS_SUMMARIES_COLUMN_MAP,
    Dataset.PerformanceMetrics: METRICS_COLUMN_MAP,
    Dataset.SpansIndexed: SPAN_COLUMN_MAP,
    Dataset.IssuePlatform: ISSUE_PLATFORM_MAP,
    Dataset.Replays: {},
}

# Store the internal field names to save work later on.
# Add `group_id` to the events dataset list as we don't want to publicly
# expose that field, but it is used by eventstore and other internals.
DATASET_FIELDS = {
    Dataset.Events: list(SENTRY_SNUBA_MAP.values()),
    Dataset.Transactions: list(TRANSACTIONS_SNUBA_MAP.values()),
    Dataset.Discover: list(DISCOVER_COLUMN_MAP.values()),
    Dataset.Sessions: SESSIONS_FIELD_LIST,
    Dataset.IssuePlatform: list(ISSUE_PLATFORM_MAP.values()),
    Dataset.SpansIndexed: list(SPAN_COLUMN_MAP.values()),
    Dataset.MetricsSummaries: list(METRICS_SUMMARIES_COLUMN_MAP.values()),
}

SNUBA_OR = "or"
SNUBA_AND = "and"
OPERATOR_TO_FUNCTION = {
    "LIKE": "like",
    "NOT LIKE": "notLike",
    "=": "equals",
    "!=": "notEquals",
    ">": "greater",
    "<": "less",
    ">=": "greaterOrEquals",
    "<=": "lessOrEquals",
    "IS NULL": "isNull",
}
FUNCTION_TO_OPERATOR = {v: k for k, v in OPERATOR_TO_FUNCTION.items()}


def parse_snuba_datetime(value):
    """Parses a datetime value from snuba."""
    return parse_datetime(value)


class SnubaError(Exception):
    pass


class UnqualifiedQueryError(SnubaError):
    """
    Exception raised when a required qualification was not satisfied in the query.
    """


class UnexpectedResponseError(SnubaError):
    """
    Exception raised when the Snuba API server returns an unexpected response
    type (e.g. not JSON.)
    """


class QueryExecutionError(SnubaError):
    """
    Exception raised when a query failed to execute.
    """


class RateLimitExceeded(SnubaError):
    """
    Exception raised when a query cannot be executed due to rate limits.
    """


class SchemaValidationError(QueryExecutionError):
    """
    Exception raised when a query is not valid.
    """


class QueryMemoryLimitExceeded(QueryExecutionError):
    """
    Exception raised when a query would exceed the memory limit.
    """


class QueryIllegalTypeOfArgument(QueryExecutionError):
    """
    Exception raised when a function in the query is provided an invalid
    argument type.
    """


class QueryMissingColumn(QueryExecutionError):
    """
    Exception raised when a column is missing.
    """


class QueryTooManySimultaneous(QueryExecutionError):
    """
    Exception raised when a query is rejected due to too many simultaneous
    queries being performed on the database.
    """


class QuerySizeExceeded(QueryExecutionError):
    """
    The generated query has exceeded the maximum length allowed by clickhouse
    """


class QueryExecutionTimeMaximum(QueryExecutionError):
    """
    The query has or will take over 30 seconds to run, exceeding the limit
    that has been set
    """


class DatasetSelectionError(QueryExecutionError):
    """
    This query has resulted in needing to check multiple datasets in a way
    that is not currently handled, clickhouse errors with data being compressed
    by different methods when this happens
    """


class QueryConnectionFailed(QueryExecutionError):
    """
    The connection to clickhouse has failed, and so the query cannot be run
    """


clickhouse_error_codes_map = {
    10: QueryMissingColumn,
    43: QueryIllegalTypeOfArgument,
    47: QueryMissingColumn,
    62: QuerySizeExceeded,
    160: QueryExecutionTimeMaximum,
    202: QueryTooManySimultaneous,
    241: QueryMemoryLimitExceeded,
    271: DatasetSelectionError,
    279: QueryConnectionFailed,
}


class QueryOutsideRetentionError(Exception):
    pass


class QueryOutsideGroupActivityError(Exception):
    pass


SnubaTSResult = namedtuple("SnubaTSResult", ("data", "start", "end", "rollup"))


@contextmanager
def timer(name, prefix="snuba.client"):
    t = time.time()
    try:
        yield
    finally:
        metrics.timing(f"{prefix}.{name}", time.time() - t)


@contextmanager
def options_override(overrides):
    """\
    NOT THREAD SAFE!

    Adds to OVERRIDE_OPTIONS, restoring previous values and removing
    keys that didn't previously exist on exit, so that calls to this
    can be nested.
    """
    previous = {}
    delete = []

    for k, v in overrides.items():
        try:
            previous[k] = OVERRIDE_OPTIONS[k]
        except KeyError:
            delete.append(k)
        OVERRIDE_OPTIONS[k] = v

    try:
        yield
    finally:
        for k, v in previous.items():
            OVERRIDE_OPTIONS[k] = v
        for k in delete:
            OVERRIDE_OPTIONS.pop(k)


class RetrySkipTimeout(urllib3.Retry):
    """
    urllib3 Retry class does not allow us to retry on read errors but to exclude
    read timeout. Retrying after a timeout adds useless load to Snuba.
    """

    def increment(
        self, method=None, url=None, response=None, error=None, _pool=None, _stacktrace=None
    ):
        """
        Just rely on the parent class unless we have a read timeout. In that case
        immediately give up
        """
        if error and isinstance(error, urllib3.exceptions.ReadTimeoutError):
            raise error.with_traceback(_stacktrace)

        metrics.incr(
            "snuba.client.retry",
            tags={"method": method, "path": urlparse(url).path if url else None},
        )
        return super().increment(
            method=method,
            url=url,
            response=response,
            error=error,
            _pool=_pool,
            _stacktrace=_stacktrace,
        )


_snuba_pool = connection_from_url(
    settings.SENTRY_SNUBA,
    retries=RetrySkipTimeout(
        total=5,
        # Our calls to snuba frequently fail due to network issues. We want to
        # automatically retry most requests. Some of our POSTs and all of our DELETEs
        # do cause mutations, but we have other things in place to handle duplicate
        # mutations.
        allowed_methods={"GET", "POST", "DELETE"},
    ),
    timeout=settings.SENTRY_SNUBA_TIMEOUT,
    maxsize=10,
)
_query_thread_pool = ThreadPoolExecutor(max_workers=10)


epoch_naive = datetime(1970, 1, 1, tzinfo=None)


def to_naive_timestamp(value):
    """
    Convert a time zone aware datetime to a POSIX timestamp (with fractional
    component.)
    """
    return (value - epoch_naive).total_seconds()


def to_start_of_hour(dt: datetime) -> str:
    """This is a function that mimics toStartOfHour from Clickhouse"""
    return dt.replace(minute=0, second=0, microsecond=0).isoformat()


def get_snuba_column_name(name, dataset=Dataset.Events):
    """
    Get corresponding Snuba column name from Sentry snuba map, if not found
    the column is assumed to be a tag. If name is falsy or name is a quoted literal
    (e.g. "'name'"), leave unchanged.
    """
    no_conversion = {"group_id", "group_ids", "project_id", "start", "end"}

    if name in no_conversion:
        return name

    if not name or name.startswith("tags[") or QUOTED_LITERAL_RE.match(name):
        return name

    measurement_name = get_measurement_name(name)
    span_op_breakdown_name = get_span_op_breakdown_name(name)
    if "measurements_key" in DATASETS[dataset] and measurement_name:
        default = f"measurements[{measurement_name}]"
    elif "span_op_breakdowns_key" in DATASETS[dataset] and span_op_breakdown_name:
        default = f"span_op_breakdowns[{span_op_breakdown_name}]"
    else:
        default = f"tags[{name}]"

    return DATASETS[dataset].get(name, default)


def get_function_index(column_expr, depth=0):
    """
    If column_expr list contains a function, returns the index of its function name
    within column_expr (and assumption is that index + 1 is the list of arguments),
    otherwise None.

     A function expression is of the form:
         [func, [arg1, arg2]]  => func(arg1, arg2)
     If a string argument is followed by list arg, the pair of them is assumed
    to be a nested function call, with extra args to the outer function afterward.
         [func1, [func2, [arg1, arg2], arg3]]  => func1(func2(arg1, arg2), arg3)
     Although at the top level, there is no outer function call, and the optional
    3rd argument is interpreted as an alias for the entire expression.
         [func, [arg1], alias] => function(arg1) AS alias
     You can also have a function part of an argument list:
         [func1, [arg1, func2, [arg2, arg3]]] => func1(arg1, func2(arg2, arg3))
    """
    index = None
    if isinstance(column_expr, (tuple, list)):
        i = 0
        while i < len(column_expr) - 1:
            # The assumption here is that a list that follows a string means
            # the string is a function name
            if isinstance(column_expr[i], str) and isinstance(column_expr[i + 1], (tuple, list)):
                assert column_expr[i] in SAFE_FUNCTIONS or SAFE_FUNCTION_RE.match(
                    column_expr[i]
                ), column_expr[i]
                index = i
                break
            else:
                i = i + 1

        return index
    else:
        return None


def get_arrayjoin(column):
    match = re.match(r"^(exception_stacks|exception_frames|contexts)\..+$", column)
    if match:
        return match.groups()[0]


def get_organization_id_from_project_ids(project_ids: Sequence[int]) -> int:
    # any project will do, as they should all be from the same organization
    try:
        # Most of the time the project should exist, so get from cache to keep it fast
        organization_id = Project.objects.get_from_cache(pk=project_ids[0]).organization_id
    except Project.DoesNotExist:
        # But in the case the first project doesn't exist, grab the first non deleted project
        project = Project.objects.filter(pk__in=project_ids).values("organization_id").first()
        if project is None:
            raise UnqualifiedQueryError("All project_ids from the filter no longer exist")
        organization_id = project.get("organization_id")

    return organization_id


def infer_project_ids_from_related_models(filter_keys: Mapping[str, Sequence[int]]) -> list[int]:
    ids = [set(get_related_project_ids(k, filter_keys[k])) for k in filter_keys]
    return list(set.union(*ids))


def get_query_params_to_update_for_projects(
    query_params: SnubaQueryParams, with_org: bool = False
) -> tuple[int, dict[str, Any]]:
    """
    Get the project ID and query params that need to be updated for project
    based datasets, before we send the query to Snuba.
    """
    if "project_id" in query_params.filter_keys:
        # If we are given a set of project ids, use those directly.
        project_ids = list(set(query_params.filter_keys["project_id"]))
    elif query_params.filter_keys:
        # Otherwise infer the project_ids from any related models
        with timer("get_related_project_ids"):
            project_ids = infer_project_ids_from_related_models(query_params.filter_keys)
    elif query_params.conditions:
        project_ids = []
        for cond in query_params.conditions:
            if cond[0] == "project_id":
                project_ids = [cond[2]] if cond[1] == "=" else cond[2]
    else:
        project_ids = []

    if not project_ids:
        raise UnqualifiedQueryError(
            "No project_id filter, or none could be inferred from other filters."
        )

    organization_id = get_organization_id_from_project_ids(project_ids)

    params: dict[str, Any] = {"project": project_ids}
    if with_org:
        params["organization"] = organization_id

    return organization_id, params


def get_query_params_to_update_for_organizations(query_params):
    """
    Get the organization ID and query params that need to be updated for organization
    based datasets, before we send the query to Snuba.
    """
    if "org_id" in query_params.filter_keys:
        organization_ids = list(set(query_params.filter_keys["org_id"]))
        if len(organization_ids) != 1:
            raise UnqualifiedQueryError("Multiple organization_ids found. Only one allowed.")
        organization_id = organization_ids[0]
    elif "project_id" in query_params.filter_keys:
        organization_id, _ = get_query_params_to_update_for_projects(query_params)
    elif "key_id" in query_params.filter_keys:
        key_ids = list(set(query_params.filter_keys["key_id"]))
        project_key = ProjectKey.objects.get(pk=key_ids[0])
        organization_id = project_key.project.organization_id
    else:
        organization_id = None

    if not organization_id:
        raise UnqualifiedQueryError(
            "No organization_id filter, or none could be inferred from other filters."
        )

    return organization_id, {"organization": organization_id}


def _prepare_start_end(
    start: datetime | None,
    end: datetime | None,
    organization_id: int,
    group_ids: Sequence[int] | None,
) -> tuple[datetime, datetime]:
    if not start:
        start = datetime(2008, 5, 8)
    if not end:
        end = datetime.utcnow() + timedelta(seconds=1)

    # convert to naive UTC datetimes, as Snuba only deals in UTC
    # and this avoids offset-naive and offset-aware issues
    start = naiveify_datetime(start)
    end = naiveify_datetime(end)

    expired, start = outside_retention_with_modified_start(
        start, end, Organization(organization_id)
    )
    if expired:
        raise QueryOutsideRetentionError("Invalid date range. Please try a more recent date range.")

    # if `shrink_time_window` pushed `start` after `end` it means the user queried
    # a Group for T1 to T2 when the group was only active for T3 to T4, so the query
    # wouldn't return any results anyway
    new_start = shrink_time_window(group_ids, start)

    # TODO (alexh) this is a quick emergency fix for an occasion where a search
    # results in only 1 django candidate, which is then passed to snuba to
    # check and we raised because of it. Remove this once we figure out why the
    # candidate was returned from django at all if it existed only outside the
    # time range of the query
    if new_start <= end:
        start = new_start

    if start > end:
        raise QueryOutsideGroupActivityError

    return start, end


def _prepare_query_params(query_params: SnubaQueryParams, referrer: str | None = None):
    kwargs = deepcopy(query_params.kwargs)
    query_params_conditions = deepcopy(query_params.conditions)

    with timer("get_snuba_map"):
        forward, reverse = get_snuba_translators(
            query_params.filter_keys, is_grouprelease=query_params.is_grouprelease
        )

    if query_params.dataset in [
        Dataset.Events,
        Dataset.Discover,
        Dataset.Sessions,
        Dataset.Transactions,
        Dataset.Replays,
        Dataset.IssuePlatform,
    ]:
        (organization_id, params_to_update) = get_query_params_to_update_for_projects(
            query_params, with_org=query_params.dataset == Dataset.Sessions
        )
    elif query_params.dataset in [Dataset.Outcomes, Dataset.OutcomesRaw]:
        (organization_id, params_to_update) = get_query_params_to_update_for_organizations(
            query_params
        )
    else:
        raise UnqualifiedQueryError(
            "No strategy found for getting an organization for the given dataset."
        )

    kwargs.update(params_to_update)

    for col, keys in forward(deepcopy(query_params.filter_keys)).items():
        if keys:
            if len(keys) == 1 and None in keys:
                query_params_conditions.append((col, "IS NULL", None))
            else:
                query_params_conditions.append((col, "IN", keys))

    start, end = _prepare_start_end(
        query_params.start,
        query_params.end,
        organization_id,
        query_params.filter_keys.get("group_id"),
    )

    kwargs.update(
        {
            "dataset": query_params.dataset.value,
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "groupby": query_params.groupby,
            "conditions": query_params_conditions,
            "aggregations": query_params.aggregations,
            "granularity": query_params.rollup,  # TODO name these things the same
        }
    )
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    if referrer:
        kwargs["tenant_ids"] = kwargs["tenant_ids"] if "tenant_ids" in kwargs else dict()
        kwargs["tenant_ids"]["referrer"] = referrer

    kwargs.update(OVERRIDE_OPTIONS)
    return kwargs, forward, reverse


class SnubaQueryParams:
    """
    Represents the information needed to make a query to Snuba.

    `start` and `end`: The beginning and end of the query time window (required)

    `groupby`: A list of column names to group by.

    `conditions`: A list of (column, operator, literal) conditions to be passed
    to the query. Conditions that we know will not have to be translated should
    be passed this way (eg tag[foo] = bar).

    `filter_keys`: A dictionary of {col: [key, ...]} that will be converted
    into "col IN (key, ...)" conditions. These are used to restrict the query to
    known sets of project/issue/environment/release etc. Appropriate
    translations (eg. from environment model ID to environment name) are
    performed on the query, and the inverse translation performed on the
    result. The project_id(s) to restrict the query to will also be
    automatically inferred from these keys.

    `aggregations` a list of (aggregation_function, column, alias) tuples to be
    passed to the query.

    The rest of the args are passed directly into the query JSON unmodified.
    See the snuba schema for details.
    """

    def __init__(
        self,
        dataset=None,
        start=None,
        end=None,
        groupby=None,
        conditions=None,
        filter_keys=None,
        aggregations=None,
        rollup=None,
        referrer=None,
        is_grouprelease=False,
        **kwargs,
    ):
        # TODO: instead of having events be the default, make dataset required.
        self.dataset = dataset or Dataset.Events
        self.start = start or datetime(
            2008, 5, 8
        )  # Date of sentry's first commit. Will be clamped to project retention
        # Snuba has end exclusive but our UI wants it generally to be inclusive.
        # This shows up in unittests: https://github.com/getsentry/sentry/pull/15939
        # We generally however require that the API user is aware of the exclusive
        # end.
        self.end = end or datetime.utcnow() + timedelta(seconds=1)
        self.groupby = groupby or []
        self.conditions = conditions or []
        self.aggregations = aggregations or []
        self.filter_keys = filter_keys or {}
        self.rollup = rollup
        self.referrer = referrer
        self.is_grouprelease = is_grouprelease
        self.kwargs = kwargs


def raw_query(
    dataset=None,
    start=None,
    end=None,
    groupby=None,
    conditions=None,
    filter_keys=None,
    aggregations=None,
    rollup=None,
    referrer=None,
    is_grouprelease=False,
    use_cache=False,
    **kwargs,
) -> Mapping[str, Any]:
    """
    Sends a query to snuba.  See `SnubaQueryParams` docstring for param
    descriptions.
    """

    if referrer:
        kwargs["tenant_ids"] = kwargs.get("tenant_ids") or dict()
        kwargs["tenant_ids"]["referrer"] = referrer

    snuba_params = SnubaQueryParams(
        dataset=dataset,
        start=start,
        end=end,
        groupby=groupby,
        conditions=conditions,
        filter_keys=filter_keys,
        aggregations=aggregations,
        rollup=rollup,
        is_grouprelease=is_grouprelease,
        **kwargs,
    )

    return bulk_raw_query([snuba_params], referrer=referrer, use_cache=use_cache)[0]


SnubaQuery = Union[Request, MutableMapping[str, Any]]
Translator = Callable[[Any], Any]
RequestQueryBody = tuple[Request, Translator, Translator]
LegacyQueryBody = tuple[MutableMapping[str, Any], Translator, Translator]
ResultSet = list[Mapping[str, Any]]  # TODO: Would be nice to make this a concrete structure


def raw_snql_query(
    request: Request,
    referrer: str | None = None,
    use_cache: bool = False,
) -> Mapping[str, Any]:
    """
    Alias for `bulk_snuba_queries`, kept for backwards compatibility.
    """
    # XXX (evanh): This function does none of the extra processing that the
    # other functions do here. It does not add any automatic conditions, format
    # results, nothing. Use at your own risk.
    return bulk_snuba_queries([request], referrer, use_cache)[0]


def bulk_snql_query(
    requests: list[Request],
    referrer: str | None = None,
    use_cache: bool = False,
) -> ResultSet:
    """
    Alias for `bulk_snuba_queries`, kept for backwards compatibility.
    """
    return bulk_snuba_queries(requests, referrer, use_cache)


def bulk_snuba_queries(
    requests: list[Request],
    referrer: str | None = None,
    use_cache: bool = False,
) -> ResultSet:
    """
    The main entrypoint to running queries in Snuba. This function accepts
    Requests for either MQL or SnQL queries and runs them on the appropriate endpoint.
    """

    metrics.incr("snql.sdk.api", tags={"referrer": referrer or "unknown"})
    if "consistent" in OVERRIDE_OPTIONS:
        for request in requests:
            request.flags.consistent = OVERRIDE_OPTIONS["consistent"]

    for request in requests:
        if referrer:
            request.tenant_ids = request.tenant_ids or dict()
            request.tenant_ids["referrer"] = referrer

    params = [(request, lambda x: x, lambda x: x) for request in requests]
    return _apply_cache_and_build_results(params, referrer=referrer, use_cache=use_cache)


# TODO: This is the endpoint that accepts legacy (non-SnQL/MQL queries)
# It should eventually be removed
def bulk_raw_query(
    snuba_param_list: Sequence[SnubaQueryParams],
    referrer: str | None = None,
    use_cache: bool | None = False,
) -> ResultSet:
    """
    Used to make queries using the (very) old JSON format for Snuba queries. Queries submitted here
    will be converted to SnQL queries before being sent to Snuba.
    """
    params = [_prepare_query_params(param, referrer) for param in snuba_param_list]
    request_bodies = [
        (json_to_snql(query, query["dataset"]), forward, reverse)
        for query, forward, reverse in params
    ]
    return _apply_cache_and_build_results(request_bodies, referrer=referrer, use_cache=use_cache)


def get_cache_key(query: SnubaQuery) -> str:
    if isinstance(query, Request):
        hashable = str(query)
    else:
        hashable = json.dumps(query)

    # sqc - Snuba Query Cache
    return f"sqc:{sha1(hashable.encode('utf-8')).hexdigest()}"


def _apply_cache_and_build_results(
    snuba_param_list: Sequence[RequestQueryBody],
    referrer: str | None = None,
    use_cache: bool | None = False,
) -> ResultSet:
    headers = {}
    validate_referrer(referrer)
    if referrer:
        headers["referer"] = referrer
    # Store the original position of the query so that we can maintain the order
    query_param_list = list(enumerate(snuba_param_list))

    results = []

    if use_cache:
        cache_keys = [get_cache_key(query_params[0]) for _, query_params in query_param_list]
        cache_data = cache.get_many(cache_keys)
        to_query: list[tuple[int, RequestQueryBody, str | None]] = []
        for (query_pos, query_params), cache_key in zip(query_param_list, cache_keys):
            cached_result = cache_data.get(cache_key)
            metric_tags = {"referrer": referrer} if referrer else None
            if cached_result is None:
                metrics.incr("snuba.query_cache.miss", tags=metric_tags)
                to_query.append((query_pos, query_params, cache_key))
            else:
                metrics.incr("snuba.query_cache.hit", tags=metric_tags)
                results.append((query_pos, json.loads(cached_result)))
    else:
        to_query = [(query_pos, query_params, None) for query_pos, query_params in query_param_list]

    if to_query:
        query_results = _bulk_snuba_query([item[1] for item in to_query], headers)
        for result, (query_pos, _, opt_cache_key) in zip(query_results, to_query):
            if opt_cache_key:
                cache.set(
                    opt_cache_key, json.dumps(result), settings.SENTRY_SNUBA_CACHE_TTL_SECONDS
                )
            results.append((query_pos, result))

    # Sort so that we get the results back in the original param list order
    results.sort()
    # Drop the sort order val
    return [result[1] for result in results]


def _bulk_snuba_query(
    snuba_param_list: Sequence[RequestQueryBody],
    headers: Mapping[str, str],
) -> ResultSet:
    query_referrer = headers.get("referer", "<unknown>")

    with sentry_sdk.start_span(
        op="snuba_query",
        description=query_referrer,
    ) as span:
        span.set_tag("snuba.num_queries", len(snuba_param_list))
        # We set both span + sdk level, this is cause 1 txn/error might query snuba more than once
        # but we still want to know a general sense of how referrers impact performance
        span.set_tag("query.referrer", query_referrer)
        sentry_sdk.set_tag("query.referrer", query_referrer)

        parent_api: str = "<missing>"
        scope = sentry_sdk.Scope.get_current_scope()
        if scope.transaction:
            parent_api = scope.transaction.name

        if len(snuba_param_list) > 1:
            query_results = list(
                _query_thread_pool.map(
                    _snuba_query,
                    [
                        (params, Hub(Hub.current), headers, parent_api)
                        for params in snuba_param_list
                    ],
                )
            )
        else:
            # No need to submit to the thread pool if we're just performing a single query
            query_results = [
                _snuba_query((snuba_param_list[0], Hub(Hub.current), headers, parent_api))
            ]

    results = []
    for index, item in enumerate(query_results):
        response, _, reverse = item
        try:
            body = json.loads(response.data, skip_trace=True)
            if SNUBA_INFO:
                if "sql" in body:
                    log_snuba_info(
                        "{}.sql:\n {}".format(
                            headers.get("referer", "<unknown>"),
                            sqlparse.format(body["sql"], reindent_aligned=True),
                        )
                    )
                if "error" in body:
                    log_snuba_info(
                        "{}.err: {}".format(headers.get("referer", "<unknown>"), body["error"])
                    )
        except ValueError:
            if response.status != 200:
                logger.exception("snuba.query.invalid-json", extra={"response.data": response.data})
                raise SnubaError("Failed to parse snuba error response")
            raise UnexpectedResponseError(f"Could not decode JSON response: {response.data!r}")

        if response.status != 200:
            _log_request_query(snuba_param_list[index][0])

            if body.get("error"):
                error = body["error"]
                if response.status == 429:
                    raise RateLimitExceeded(error["message"])
                elif error["type"] == "schema":
                    raise SchemaValidationError(error["message"])
                elif error["type"] == "clickhouse":
                    raise clickhouse_error_codes_map.get(error["code"], QueryExecutionError)(
                        error["message"]
                    )
                else:
                    raise SnubaError(error["message"])
            else:
                raise SnubaError(f"HTTP {response.status}")

        # Forward and reverse translation maps from model ids to snuba keys, per column
        body["data"] = [reverse(d) for d in body["data"]]
        results.append(body)

    return results


def _log_request_query(req: Request) -> None:
    """Given a request, logs its associated query in sentry breadcrumbs"""
    query_str = req.serialize()
    query_type = "mql" if isinstance(req.query, MetricsQuery) else "snql"
    sentry_sdk.add_breadcrumb(
        category="query_info",
        level="info",
        message=f"{query_type}_query",
        data={query_type: query_str},
    )


RawResult = tuple[urllib3.response.HTTPResponse, Callable[[Any], Any], Callable[[Any], Any]]


def _snql_query(params: tuple[RequestQueryBody, Hub, Mapping[str, str], str]) -> RawResult:
    # TODO: For backwards compatibility. Some modules in Sentry use this function directly (despite it being marked private).
    return _snuba_query(params)


def _snuba_query(
    params: tuple[
        RequestQueryBody,
        Hub,
        Mapping[str, str],
        str,
    ],
) -> RawResult:
    # Eventually we can get rid of this wrapper, but for now it's cleaner to unwrap
    # the params here than in the calling function. (bc of thread .map)
    query_body, thread_hub, headers, parent_api = params
    request, forward, reverse = query_body
    request.parent_api = parent_api
    try:
        referrer = headers.get("referer", "unknown")
        if SNUBA_INFO:
            import pprint

            log_snuba_info(f"{referrer}.body:\n {pprint.pformat(request.to_dict())}")
            request.flags.debug = True

        if isinstance(request.query, MetricsQuery):
            return _raw_mql_query(request, thread_hub, headers), forward, reverse

        return _raw_snql_query(request, thread_hub, headers), forward, reverse
    except urllib3.exceptions.HTTPError as err:
        raise SnubaError(err)


def _raw_mql_query(
    request: Request, thread_hub: Hub, headers: Mapping[str, str]
) -> urllib3.response.HTTPResponse:
    # Enter hub such that http spans are properly nested
    with thread_hub, timer("mql_query"):
        referrer = headers.get("referer", "unknown")
        # TODO: This can be changed back to just `serialize` after we remove SnQL support for MetricsQuery
        serialized_req = request.serialize()
        with thread_hub.start_span(op="snuba_mql.validation", description=referrer) as span:
            span.set_tag("snuba.referrer", referrer)
            body = serialized_req

        with thread_hub.start_span(op="snuba_mql.run", description=serialized_req) as span:
            span.set_tag("snuba.referrer", referrer)
            return _snuba_pool.urlopen(
                "POST", f"/{request.dataset}/mql", body=body, headers=headers
            )


def _raw_snql_query(
    request: Request, thread_hub: Hub, headers: Mapping[str, str]
) -> urllib3.response.HTTPResponse:
    # Enter hub such that http spans are properly nested
    with thread_hub, timer("snql_query"):
        referrer = headers.get("referer", "<unknown>")
        serialized_req = request.serialize()
        with thread_hub.start_span(op="snuba_snql.validation", description=referrer) as span:
            span.set_tag("snuba.referrer", referrer)
            body = serialized_req

        with thread_hub.start_span(op="snuba_snql.run", description=serialized_req) as span:
            span.set_tag("snuba.referrer", referrer)
            return _snuba_pool.urlopen(
                "POST", f"/{request.dataset}/snql", body=body, headers=headers
            )


def query(
    dataset=None,
    start=None,
    end=None,
    groupby=None,
    conditions=None,
    filter_keys=None,
    aggregations=None,
    selected_columns=None,
    totals=None,
    use_cache=False,
    **kwargs,
):
    aggregations = aggregations or [["count()", "", "aggregate"]]
    filter_keys = filter_keys or {}
    selected_columns = selected_columns or []
    groupby = groupby or []

    try:
        body = raw_query(
            dataset=dataset,
            start=start,
            end=end,
            groupby=groupby,
            conditions=conditions,
            filter_keys=filter_keys,
            aggregations=aggregations,
            selected_columns=selected_columns,
            totals=totals,
            use_cache=use_cache,
            **kwargs,
        )
    except (QueryOutsideRetentionError, QueryOutsideGroupActivityError):
        if totals:
            return {}, {}
        else:
            return {}

    # Validate and scrub response, and translate snuba keys back to IDs
    aggregate_names = [a[2] for a in aggregations]
    selected_names = [c[2] if isinstance(c, (list, tuple)) else c for c in selected_columns]
    expected_cols = set(groupby + aggregate_names + selected_names)
    got_cols = {c["name"] for c in body["meta"]}

    assert expected_cols == got_cols, f"expected {expected_cols}, got {got_cols}"

    with timer("process_result"):
        if totals:
            return (
                nest_groups(body["data"], groupby, aggregate_names + selected_names),
                body["totals"],
            )
        else:
            return nest_groups(body["data"], groupby, aggregate_names + selected_names)


def nest_groups(
    data: Sequence[MutableMapping],
    groups: Sequence[str] | None,
    aggregate_cols: Sequence[str],
) -> dict | Any:
    """
    Build a nested mapping from query response rows. Each group column
    gives a new level of nesting and the leaf result is the aggregate
    """
    if not groups:
        # At leaf level, just return the aggregations from the first data row
        if len(aggregate_cols) == 1:
            # Special case, if there is only one aggregate, just return the raw value
            return data[0][aggregate_cols[0]] if data else None
        else:
            return {c: data[0][c] for c in aggregate_cols} if data else None
    else:
        g, rest = groups[0], groups[1:]
        inter: dict[Any, Any] = {}
        for d in data:
            inter.setdefault(d[g], []).append(d)
        return {k: nest_groups(v, rest, aggregate_cols) for k, v in inter.items()}


def resolve_column(dataset) -> Callable:
    def _resolve_column(col):
        if col is None:
            return col
        if isinstance(col, int) or isinstance(col, float):
            return col
        if isinstance(col, str) and (col.startswith("tags[") or QUOTED_LITERAL_RE.match(col)):
            return col

        # Some dataset specific logic:
        if dataset == Dataset.Discover:
            if isinstance(col, (list, tuple)) or col in ("project_id", "group_id"):
                return col
        elif (
            dataset == Dataset.SpansIndexed
            and isinstance(col, str)
            and col.startswith("sentry_tags[")
        ):
            return col
        else:
            if (
                col in DATASET_FIELDS[dataset]
            ):  # Discover does not allow internal aliases to be used by customers, so doesn't get this logic.
                return col

        if col in DATASETS[dataset]:
            return DATASETS[dataset][col]

        measurement_name = get_measurement_name(col)
        if "measurements_key" in DATASETS[dataset] and measurement_name:
            return f"measurements[{measurement_name}]"

        span_op_breakdown_name = get_span_op_breakdown_name(col)
        if "span_op_breakdowns_key" in DATASETS[dataset] and span_op_breakdown_name:
            return f"span_op_breakdowns[{span_op_breakdown_name}]"

        return f"tags[{col}]"

    return _resolve_column


def resolve_condition(cond: list, column_resolver: Callable[[Any], Any]) -> list:
    """
    When conditions have been parsed by the api.event_search module
    we can end up with conditions that are not valid on the current dataset
    due to how api.event_search checks for valid field names without
    being aware of the dataset.

    We have the dataset context here, so we need to re-scope conditions to the
    current dataset.

    cond (tuple) Condition to resolve aliases in.
    column_resolver (Function[string]) Function to resolve column names for the
                                       current dataset.
    """
    index = get_function_index(cond)

    def _passthrough_arg(arg):
        if isinstance(arg, str):
            return f"'{arg}'"
        elif isinstance(arg, datetime):
            return f"'{arg.isoformat()}'"
        else:
            return arg

    # IN/NOT IN conditions are detected as a function but aren't really.
    if index is not None and cond[index] not in ("IN", "NOT IN"):
        if cond[index] in FUNCTION_TO_OPERATOR:
            func_args = cond[index + 1]
            for i, arg in enumerate(func_args):
                if i == 0:
                    if isinstance(arg, list):
                        func_args[i] = resolve_condition(arg, column_resolver)
                    else:
                        func_args[i] = column_resolver(arg)
                else:
                    func_args[i] = _passthrough_arg(arg)

            cond[index + 1] = func_args
            return cond

        func_args = cond[index + 1]
        for i, arg in enumerate(func_args):
            # Nested function
            try:
                if isinstance(arg, list):
                    func_args[i] = resolve_condition(arg, column_resolver)
                else:
                    func_args[i] = column_resolver(arg)
            except AttributeError:
                func_args[i] = _passthrough_arg(arg)
        cond[index + 1] = func_args
        return cond

    # No function name found
    if isinstance(cond, (list, tuple)) and len(cond):
        # Condition is [col, operator, value]
        if isinstance(cond[0], str) and len(cond) == 3:
            cond[0] = column_resolver(cond[0])
            return cond
        if isinstance(cond[0], list):
            if get_function_index(cond[0]) is not None:
                cond[0] = resolve_condition(cond[0], column_resolver)
                return cond
            else:
                # Nested conditions
                return [resolve_condition(item, column_resolver) for item in cond]
    raise ValueError("Unexpected condition format %s" % cond)


def aliased_query(**kwargs):
    """
    Wrapper around raw_query that selects the dataset based on the
    selected_columns, conditions and groupby parameters.
    Useful for taking arbitrary end user queries and running
    them on one of the snuba datasets.

    This function will also resolve column aliases to match the selected dataset

    This method should be used sparingly. Instead prefer to use sentry.eventstore
    sentry.tagstore, or sentry.snuba.discover instead when reading data.
    """
    with sentry_sdk.start_span(op="sentry.snuba.aliased_query"):
        return _aliased_query_impl(**kwargs)


def _aliased_query_impl(**kwargs):
    return raw_query(**aliased_query_params(**kwargs))


def resolve_conditions(
    conditions: Sequence | None, column_resolver: Callable[[Any], Any]
) -> list | None:
    if conditions is None:
        return conditions

    replacement_conditions = []
    for condition in conditions:
        replacement = resolve_condition(deepcopy(condition), column_resolver)
        if replacement:
            replacement_conditions.append(replacement)

    return replacement_conditions


def aliased_query_params(
    start=None,
    end=None,
    groupby=None,
    conditions=None,
    filter_keys=None,
    aggregations=None,
    selected_columns=None,
    arrayjoin=None,
    having=None,
    dataset=None,
    orderby=None,
    condition_resolver: Callable | None = None,
    **kwargs,
) -> dict[str, Any]:
    if dataset is None:
        raise ValueError("A dataset is required, and is no longer automatically detected.")

    derived_columns = []
    resolve_func = resolve_column(dataset)
    if selected_columns:
        for i, col in enumerate(selected_columns):
            if isinstance(col, (list, tuple)):
                derived_columns.append(col[2])
            else:
                selected_columns[i] = resolve_func(col)
        selected_columns = [c for c in selected_columns if c]

    if aggregations:
        for aggregation in aggregations:
            derived_columns.append(aggregation[2])

    if conditions:
        if condition_resolver:
            column_resolver: Callable = functools.partial(condition_resolver, dataset=dataset)
        else:
            column_resolver = resolve_func
        resolved_conditions = resolve_conditions(conditions, column_resolver)
    else:
        resolved_conditions = conditions

    if orderby:
        # Don't mutate in case we have a default order passed.
        updated_order = []
        for i, order in enumerate(orderby):
            order_field = order.lstrip("-")
            if order_field not in derived_columns:
                order_field = resolve_func(order_field)
            updated_order.append("{}{}".format("-" if order.startswith("-") else "", order_field))
        orderby = updated_order

    return dict(
        start=start,
        end=end,
        groupby=groupby,
        conditions=resolved_conditions,
        aggregations=aggregations,
        selected_columns=selected_columns,
        filter_keys=filter_keys,
        arrayjoin=arrayjoin,
        having=having,
        dataset=dataset,
        orderby=orderby,
        condition_resolver=condition_resolver,
        **kwargs,
    )


# TODO (evanh) Since we are assuming that all string values are columns,
# this will get tricky if we ever have complex columns where there are
# string arguments to the functions that aren't columns
def resolve_complex_column(col, resolve_func, ignored):
    args = col[1]

    for i in range(len(args)):
        if isinstance(args[i], (list, tuple)):
            resolve_complex_column(args[i], resolve_func, ignored)
        elif isinstance(args[i], str) and args[i] not in ignored:
            args[i] = resolve_func(args[i])


JSON_TYPE_MAP = {
    "UInt8": "boolean",
    "UInt16": "integer",
    "UInt32": "integer",
    "UInt64": "integer",
    "Float32": "number",
    "Float64": "number",
    "DateTime": "date",
}


def get_json_type(snuba_type):
    """
    Convert Snuba/Clickhouse type to JSON type
    Default is string
    """
    if snuba_type is None:
        return "string"

    # Ignore Nullable part
    if snuba_type.startswith("Nullable("):
        snuba_type = snuba_type[9:-1]

    if snuba_type.startswith("Array("):
        return "array"

    # timestamp is DateTime, whereas toStartOf{Hour,Day} are
    # DateTime('UTC') or DateTime('Universal')
    if snuba_type.startswith("DateTime("):
        return "date"

    return JSON_TYPE_MAP.get(snuba_type, "string")


# The following are functions for resolving information from sentry models
# about projects, environments, and issues (groups). Having this snuba
# implementation have to know about these relationships is not ideal, and
# many of these relationships (eg environment id->name) will have already
# been queried and exist somewhere in the call stack, but for now, lookup
# is implemented here for simplicity.


def get_snuba_translators(filter_keys, is_grouprelease=False):
    """
    Some models are stored differently in snuba, eg. as the environment
    name instead of the environment ID. Here we create and return forward()
    and reverse() translation functions that perform all the required changes.

    forward() is designed to work on the filter_keys and so should be called
    with a map of {column: [key1, key2], ...} and should return an updated map
    with the filter keys replaced with the ones that Snuba expects.

    reverse() is designed to work on result rows, so should be called with a row
    in the form {column: value, ...} and will return a translated result row.

    Because translation can potentially rely on combinations of different parts
    of the result row, I decided to implement them as composable functions over the
    row to be translated. This should make it simpler to add any other needed
    translations as long as you can express them as forward(filters) and reverse(row)
    functions.
    """

    # Helper lambdas to compose translator functions
    identity = lambda x: x
    compose = lambda f, g: lambda x: f(g(x))
    replace = lambda d, key, val: d.update({key: val}) or d

    forward = identity
    reverse = identity

    map_columns = {
        "environment": (Environment, "name", lambda name: None if name == "" else name),
        "tags[sentry:release]": (Release, "version", identity),
        "release": (Release, "version", identity),
    }

    for col, (model, field, fmt) in map_columns.items():
        fwd, rev = None, None
        ids = filter_keys.get(col)
        if not ids:
            continue
        if is_grouprelease and col in ("release", "tags[sentry:release]"):
            # GroupRelease -> Release translation is a special case because the
            # translation relies on both the Group and Release value in the result row.
            #
            # We create a map of {grouprelease_id: (group_id, version), ...} and the corresponding
            # reverse map of {(group_id, version): grouprelease_id, ...}
            # NB this does depend on `issue` being defined in the query result, and the correct
            # set of issues being resolved, which is outside the control of this function.
            gr_map = GroupRelease.objects.filter(id__in=ids).values_list(
                "id", "group_id", "release_id"
            )
            ver = dict(
                Release.objects.filter(id__in=[x[2] for x in gr_map]).values_list("id", "version")
            )
            fwd_map = {gr: (group, ver[release]) for (gr, group, release) in gr_map}
            rev_map = {v: k for k, v in fwd_map.items()}
            fwd = (
                lambda col, trans: lambda filters: replace(
                    filters, col, [trans[k][1] for k in filters[col]]
                )
            )(col, fwd_map)
            rev = (
                lambda col, trans: lambda row: replace(
                    # The translate map may not have every combination of issue/release
                    # returned by the query.
                    row,
                    col,
                    trans.get((row["group_id"], row[col])),
                )
            )(col, rev_map)

        else:
            fwd_map = {
                k: fmt(v) for k, v in model.objects.filter(id__in=ids).values_list("id", field)
            }
            rev_map = {v: k for k, v in fwd_map.items()}
            fwd = (
                lambda col, trans: lambda filters: replace(
                    filters, col, [trans[k] for k in filters[col] if k]
                )
            )(col, fwd_map)
            rev = (
                lambda col, trans: lambda row: (
                    replace(row, col, trans[row[col]]) if col in row else row
                )
            )(col, rev_map)

        if fwd is not None:
            forward = compose(forward, fwd)
        if rev is not None:
            reverse = compose(reverse, rev)

    # Extra reverse translator for time column.
    reverse = compose(
        reverse,
        lambda row: (
            replace(row, "time", int(parse_datetime(row["time"]).timestamp()))
            if "time" in row
            else row
        ),
    )
    # Extra reverse translator for bucketed_end column.
    reverse = compose(
        reverse,
        lambda row: (
            replace(row, "bucketed_end", int(parse_datetime(row["bucketed_end"]).timestamp()))
            if "bucketed_end" in row
            else row
        ),
    )

    return (forward, reverse)


def get_related_project_ids(column, ids):
    """
    Get the project_ids from a model that has a foreign key to project.
    """
    mappings = {
        "group_id": (Group, "id", "project_id"),
        "tags[sentry:release]": (ReleaseProject, "release_id", "project_id"),
        "release": (ReleaseProject, "release_id", "project_id"),
    }
    if ids:
        if column == "project_id":
            return ids
        elif column in mappings:
            model, id_field, project_field = mappings[column]
            return model.objects.filter(
                **{id_field + "__in": ids, project_field + "__isnull": False}
            ).values_list(project_field, flat=True)
    return []


def shrink_time_window(issues: Collection | None, start: datetime) -> datetime:
    """\
    If a single issue is passed in, shrink the `start` parameter to be briefly before
    the `first_seen` in order to hopefully eliminate a large percentage of rows scanned.

    Note that we don't shrink `end` based on `last_seen` because that value is updated
    asynchronously by buffers, and will cause queries to skip recently seen data on
    stale groups.
    """
    if issues and len(issues) == 1:
        try:
            group = Group.objects.get(pk=list(issues)[0])
            start = max(start, naiveify_datetime(group.first_seen) - timedelta(minutes=5))
        except Group.DoesNotExist:
            return start

    return start


def naiveify_datetime(dt: datetime) -> datetime:
    return dt if not dt.tzinfo else dt.astimezone(timezone.utc).replace(tzinfo=None)


def quantize_time(time, key_hash, duration=300, rounding=ROUND_DOWN):
    """Adds jitter based on the key_hash around start/end times for caching snuba queries

    Given a time and a key_hash this should result in a timestamp that remains the same for a duration
    The end of the duration will be different per key_hash which avoids spikes in the number of queries
    Must be based on the key_hash so they cache keys are consistent per query

    For example: the time is 17:02:00, there's two queries query A has a key_hash of 30, query B has a key_hash of
    60, we have the default duration of 300 (5 Minutes)
    - query A will have the suffix of 17:00:30 for a timewindow from 17:00:30 until 17:05:30
        - eg. Even when its 17:05:00 the suffix will still be 17:00:30
    - query B will have the suffix of 17:01:00 for a timewindow from 17:01:00 until 17:06:00
    """
    # Use the hash so that seconds past the hour gets rounded differently per query.
    jitter = key_hash % duration
    seconds_past_hour = time.minute * 60 + time.second
    # Round seconds to a multiple of duration, because this uses "floor" division shouldn't give us a future window
    time_window_start = seconds_past_hour // duration * duration + jitter
    # If the time is past the rounded seconds then we want our key to be for this timewindow
    if time_window_start < seconds_past_hour:
        seconds_past_hour = time_window_start
    # Otherwise we're in the previous time window, subtract duration to give us the previous timewindows start
    else:
        seconds_past_hour = time_window_start - duration

    if rounding == ROUND_UP:
        seconds_past_hour += duration

    return (
        # Since we're adding seconds past the hour, we want time but without minutes or seconds
        time.replace(minute=0, second=0, microsecond=0)
        +
        # Use timedelta here so keys are consistent around hour boundaries
        timedelta(seconds=seconds_past_hour)
    )


def is_measurement(key):
    return isinstance(key, str) and MEASUREMENTS_KEY_RE.match(key)


def is_duration_measurement(key):
    return key in [
        "measurements.fp",
        "measurements.fcp",
        "measurements.lcp",
        "measurements.fid",
        "measurements.ttfb",
        "measurements.ttfb.requesttime",
        "measurements.time_to_initial_display",
        "measurements.time_to_full_display",
        "measurements.app_start_cold",
        "measurements.app_start_warm",
        "measurements.time_to_full_display",
        "measurements.time_to_initial_display",
    ]


def is_percentage_measurement(key):
    return key in [
        "measurements.frames_slow_rate",
        "measurements.frames_frozen_rate",
        "measurements.stall_percentage",
    ]


def is_numeric_measurement(key):
    return key in [
        "measurements.cls",
        "measurements.frames_frozen",
        "measurements.frames_slow",
        "measurements.frames_total",
        "measurements.stall_count",
    ]


def is_span_op_breakdown(key):
    return isinstance(key, str) and get_span_op_breakdown_name(key) is not None


def get_measurement_name(measurement):
    match = MEASUREMENTS_KEY_RE.match(measurement)
    return match.group(1).lower() if match else None


def get_span_op_breakdown_name(breakdown):
    match = SPAN_OP_BREAKDOWNS_FIELD_RE.match(breakdown)
    if match:
        breakdown_key = match.group(1).lower()
        if breakdown_key == "total.time":
            return breakdown_key
        return f"ops.{breakdown_key}"
    return None


def get_array_column_alias(array_column):
    # array column prefix may be aliased differently to the user (i.e. the product)
    if array_column == "span_op_breakdowns":
        return "spans"
    return array_column


def get_span_op_breakdown_key_name(breakdown_key):
    match = SPAN_OP_BREAKDOWNS_KEY_RE.match(breakdown_key)
    return match.group(1).lower() if match else breakdown_key


def get_array_column_field(array_column, internal_key):
    if array_column == "span_op_breakdowns":
        return get_span_op_breakdown_key_name(internal_key)
    return internal_key
