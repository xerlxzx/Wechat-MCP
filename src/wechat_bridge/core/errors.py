"""Public errors contain codes, never source material."""

class BridgeError(Exception):
    def __init__(self, code: str, *, native_code: int | None = None):
        self.code = code
        self.native_code = native_code
        super().__init__(code)

    def public_dict(self) -> dict:
        return {"error": self.code}
