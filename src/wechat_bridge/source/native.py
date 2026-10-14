"""Typed, narrow bindings to an explicitly selected SQLCipher library."""
import ctypes as C
from pathlib import Path
from ..core.errors import BridgeError

AUTHORIZER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_char_p)
PROGRESS = C.CFUNCTYPE(C.c_int, C.c_void_p)


def load_native(path: Path) -> C.CDLL:
    """Load the library and declare only the calls the reader uses; raises sqlcipher_unavailable."""
    try:
        lib = C.CDLL(str(path))
        definitions = {
            "sqlite3_libversion": ([], C.c_char_p),
            "sqlite3_open_v2": ([C.c_char_p, C.POINTER(C.c_void_p), C.c_int, C.c_char_p], C.c_int),
            "sqlite3_key": ([C.c_void_p, C.c_void_p, C.c_int], C.c_int),
            "sqlite3_close_v2": ([C.c_void_p], C.c_int),
            "sqlite3_busy_timeout": ([C.c_void_p, C.c_int], C.c_int),
            "sqlite3_prepare_v2": ([C.c_void_p, C.c_char_p, C.c_int, C.POINTER(C.c_void_p), C.POINTER(C.c_char_p)], C.c_int),
            "sqlite3_bind_parameter_count": ([C.c_void_p], C.c_int),
            "sqlite3_bind_null": ([C.c_void_p, C.c_int], C.c_int),
            "sqlite3_bind_int64": ([C.c_void_p, C.c_int, C.c_int64], C.c_int),
            "sqlite3_bind_double": ([C.c_void_p, C.c_int, C.c_double], C.c_int),
            "sqlite3_bind_text": ([C.c_void_p, C.c_int, C.c_char_p, C.c_int, C.c_void_p], C.c_int),
            "sqlite3_bind_blob": ([C.c_void_p, C.c_int, C.c_void_p, C.c_int, C.c_void_p], C.c_int),
            "sqlite3_step": ([C.c_void_p], C.c_int),
            "sqlite3_finalize": ([C.c_void_p], C.c_int),
            "sqlite3_column_count": ([C.c_void_p], C.c_int),
            "sqlite3_column_name": ([C.c_void_p, C.c_int], C.c_char_p),
            "sqlite3_column_type": ([C.c_void_p, C.c_int], C.c_int),
            "sqlite3_column_int64": ([C.c_void_p, C.c_int], C.c_int64),
            "sqlite3_column_double": ([C.c_void_p, C.c_int], C.c_double),
            "sqlite3_column_text": ([C.c_void_p, C.c_int], C.c_void_p),
            "sqlite3_column_blob": ([C.c_void_p, C.c_int], C.c_void_p),
            "sqlite3_column_bytes": ([C.c_void_p, C.c_int], C.c_int),
            "sqlite3_set_authorizer": ([C.c_void_p, AUTHORIZER, C.c_void_p], C.c_int),
            "sqlite3_progress_handler": ([C.c_void_p, C.c_int, PROGRESS, C.c_void_p], None),
            "sqlite3_enable_load_extension": ([C.c_void_p, C.c_int], C.c_int),
        }
        for name, (args, result) in definitions.items():
            fn = getattr(lib, name); fn.argtypes = args; fn.restype = result
        return lib
    except (OSError, AttributeError):
        raise BridgeError("sqlcipher_unavailable") from None
