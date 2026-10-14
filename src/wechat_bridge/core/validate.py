"""Small shared input validators that raise BridgeError with a caller-chosen code."""
from .errors import BridgeError


def require_int_range(value, low, high, code):
    """Return value if it is an int (not bool) in [low, high]; otherwise raise BridgeError(code)."""
    # `type(value) is int`, not isinstance, so bool is rejected -- matching every
    # call site's original strict check. The caller supplies its own error code.
    if type(value) is not int or not low <= value <= high:
        raise BridgeError(code)
    return value
