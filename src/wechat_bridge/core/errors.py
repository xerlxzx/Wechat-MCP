"""Public errors contain codes, never source material."""

class BridgeError(Exception):
    """Snake_case error code; native_code carries the SQLite result code when one applies."""
    def __init__(self, code: str, *, native_code: int | None = None):
        self.code = code
        self.native_code = native_code
        super().__init__(code)

    def public_dict(self) -> dict:
        """Wire shape for callers: the code only."""
        return {"error": self.code}
