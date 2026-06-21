class BaseConnector:
    """Base class for live external-API connectors (as opposed to file-based ingestion)."""

    def sync(self) -> dict:
        raise NotImplementedError
