"""Independent writable SQLCipher fixture/oracle; never imported by production."""
import ctypes as C
from pathlib import Path

LIBRARY = Path.home() / ".local/share/wechat-bridge/sqlcipher-4.19.0/lib/libsqlcipher.dylib"
KEY = bytes(range(32))

class Engine:
    def __init__(self, path, key=KEY, *, readonly=False):
        self.lib = C.CDLL(str(LIBRARY))
        self.lib.sqlite3_open_v2.argtypes = [C.c_char_p, C.POINTER(C.c_void_p), C.c_int, C.c_char_p]
        self.lib.sqlite3_key.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
        self.lib.sqlite3_exec.argtypes = [C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p]
        self.lib.sqlite3_close.argtypes = [C.c_void_p]
        self.ptr = C.c_void_p()
        assert self.lib.sqlite3_open_v2(str(path).encode(), C.byref(self.ptr), 1 if readonly else 6, None) == 0
        raw = ("x'" + key.hex() + "'").encode()
        assert self.lib.sqlite3_key(self.ptr, raw, len(raw)) == 0
        self.run("PRAGMA cipher_compatibility=4")

    def run(self, sql):
        rows = []
        callback_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int,
                                   C.POINTER(C.c_char_p), C.POINTER(C.c_char_p))
        def collect(_, n, values, names):
            rows.append({names[i].decode(): values[i].decode() if values[i] is not None else None for i in range(n)})
            return 0
        callback = callback_type(collect)
        rc = self.lib.sqlite3_exec(self.ptr, sql.encode(), callback, None, None)
        if rc:
            raise RuntimeError(f"fixture_sqlite_{rc}")
        return rows

    def close(self):
        if self.ptr:
            assert self.lib.sqlite3_close(self.ptr) == 0
            self.ptr = C.c_void_p()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def create_database(path, *, wal=True, key=KEY):
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = Engine(path, key)
    if wal:
        engine.run("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0")
    engine.run("CREATE TABLE messages(id INTEGER PRIMARY KEY, text TEXT, payload BLOB)")
    engine.run("INSERT INTO messages VALUES(1, '发票 🐈', x'00ff00')")
    return engine
