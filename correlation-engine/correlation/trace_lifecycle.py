from datetime import datetime, timezone


class TraceLifecycleManager:
    """
    Controla fechamento de traces inativos.
    """


    def __init__(
        self,
        timeout_seconds=5
    ):

        self.timeout_seconds = (
            timeout_seconds
        )



    def should_finish(
        self,
        trace
    ):

        if trace.last_updated is None:
            return False


        now = datetime.now(
            timezone.utc
        )


        elapsed = (
            now -
            trace.last_updated
        ).total_seconds()


        return (
            elapsed >=
            self.timeout_seconds
        )