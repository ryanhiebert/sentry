import abc
from typing import cast

from sentry.services.hybrid_cloud import silo_mode_delegation
from sentry.services.hybrid_cloud.rpc import RpcService, rpc_method
from sentry.silo import SiloMode

from .model import AuditLogEvent, UserIpEvent


class LogService(RpcService):
    key = "integration"
    local_mode = SiloMode.CONTROL

    @classmethod
    def get_local_implementation(cls) -> RpcService:
        return impl_by_db()

    @rpc_method
    @abc.abstractmethod
    def record_audit_log(self, *, event: AuditLogEvent) -> None:
        pass

    @rpc_method
    @abc.abstractmethod
    def record_user_ip(self, *, event: UserIpEvent) -> None:
        pass


def impl_by_db() -> LogService:
    from .impl import DatabaseBackedLogService

    return DatabaseBackedLogService()


def impl_by_outbox() -> LogService:
    from .impl import OutboxBackedLogService

    return OutboxBackedLogService()


# An asynchronous service which can delegate to an outbox implementation, essentially enqueueing
# delivery of log entries for future processing.
log_service: LogService = silo_mode_delegation(
    {
        SiloMode.REGION: impl_by_outbox,
        SiloMode.CONTROL: impl_by_db,
        SiloMode.MONOLITH: impl_by_db,
    }
)

# A synchronous service which can delegate to a remote rpc endpoint.  Used by the outbox receiver
# that handles rows generated by the asynchronous log_service implementation.
log_rpc_service: LogService = cast(LogService, LogService.create_delegation())
