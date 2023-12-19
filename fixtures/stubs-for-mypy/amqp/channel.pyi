from typing_extensions import Self

class Channel:
    def __enter__(self) -> Self: ...
    def __exit__(self, *a: object) -> None: ...
    def queue_declare(self, queue: str = ..., passive: bool = ...) -> tuple[str, int, int]: ...
    def queue_purge(self, queue: str = ...) -> int: ...
