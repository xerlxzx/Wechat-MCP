"""Read-only, bounded SQLCipher transactions. No writable production mode."""
from contextlib import contextmanager
import ctypes as C
import os
import sqlite3 as constants
import signal
import threading
import time
from typing import TypeAlias

from ..core.config import Config
from ..core.errors import BridgeError
from .discovery import SourceDb, source_path
from .native import AUTHORIZER, PROGRESS, load_native

SqlValue: TypeAlias = None | int | float | str | bytes
Row: TypeAlias = dict[str, SqlValue]
READ_PRAGMAS = frozenset((b"table_info", b"table_xinfo", b"index_list", b"index_info",
                         b"query_only", b"cipher_version", b"schema_version", b"data_version",
                         b"cipher_integrity_check", b"integrity_check"))
TABLE_PRAGMAS = frozenset((b"table_info", b"table_xinfo", b"index_list", b"index_info"))
FUNCTIONS = frozenset((b"count", b"max", b"min", b"sum", b"coalesce", b"ifnull", b"hex",
                       b"length", b"lower", b"upper", b"substr", b"abs", b"typeof", b"zeroblob"))


class ReadTransaction:
    """Handle for queries inside one read transaction; inactive once the transaction ends."""
    def __init__(self, reader):
        self._reader = reader
        self.active = True

    def query(self, sql: str, params: tuple[SqlValue, ...] = (), *, max_rows: int = 200) -> list[Row]:
        """Run one parameterized statement; raises transaction_closed or the reader's query errors."""
        if not self.active:
            raise BridgeError("transaction_closed")
        return self._reader._execute(sql, params, max_rows=max_rows)


class SourceReader:
    """One read-only SQLCipher connection to a source database.

    An authorizer denies everything but SELECT, whitelisted functions and read pragmas.
    Each query has a 5 s deadline, at most 200 rows and 8 MiB of text and blob data.
    """
    query_timeout = 5.0

    def __init__(self, db: SourceDb, raw_key: bytes, config: Config, profile: str):
        self.db = db
        self.ptr = C.c_void_p()
        self._transaction = None
        self._control = False
        self._deadline = float("inf")
        if profile != "sqlcipher4" or not isinstance(raw_key, bytes) or len(raw_key) != 32:
            raise BridgeError("invalid_cipher_configuration")
        self.lib = load_native(config.sqlcipher_library)
        path = source_path(db, config)
        # Checked before and after keying so a swapped or replaced file is never read.
        def validate_identity():
            try:
                stat = path.stat()
                with path.open("rb") as stream:
                    salt = stream.read(16).hex()
                if (stat.st_dev, stat.st_ino) != db.file_identity or salt != db.salt_hex:
                    raise BridgeError("source_changed")
            except OSError:
                raise BridgeError("source_unreadable") from None
        validate_identity()
        self._authorizer = AUTHORIZER(self._authorize)
        self._cancel_requested = False
        self._callback_error = None
        self._progress = PROGRESS(self._on_progress)
        try:
            self._check(self.lib.sqlite3_open_v2(os.fsencode(path), C.byref(self.ptr), 1, None))
            self._check(self.lib.sqlite3_busy_timeout(self.ptr, 2000))
            key_argument = ("x'" + raw_key.hex() + "'").encode("ascii")
            self._check(self.lib.sqlite3_key(self.ptr, key_argument, len(key_argument)))
            self._execute("PRAGMA cipher_compatibility=4")
            self._execute("PRAGMA cipher_log_level=NONE")
            self._execute("PRAGMA query_only=ON")
            self.cipher_version = self._execute("PRAGMA cipher_version")[0]["cipher_version"]
            if not str(self.cipher_version).startswith("4."):
                raise BridgeError("unsupported_cipher_version")
            self.lib.sqlite3_progress_handler(self.ptr, 1000, self._progress, None)
            self._execute("SELECT count(*) AS n FROM sqlite_schema")
            validate_identity()
            self._check(self.lib.sqlite3_enable_load_extension(self.ptr, 0))
            self._check(self.lib.sqlite3_set_authorizer(self.ptr, self._authorizer, None))
        except BaseException:
            self.close()
            raise

    # Default-deny: anything not listed here (writes, ATTACH, load_extension) is refused.
    def _authorize(self, _, action, arg1, arg2, db, origin):
        if action in (constants.SQLITE_SELECT, constants.SQLITE_READ, constants.SQLITE_RECURSIVE):
            return 0
        if action == constants.SQLITE_FUNCTION and arg2 and arg2.lower() in FUNCTIONS:
            return 0
        if action == constants.SQLITE_TRANSACTION and self._control:
            return 0
        if action == constants.SQLITE_PRAGMA:
            name = (arg1 or b"").lower()
            if name in READ_PRAGMAS and (arg2 is None or name in TABLE_PRAGMAS):
                return 0
        return constants.SQLITE_DENY

    def _on_progress(self, _):
        try:
            return int(self._cancel_requested or time.monotonic() > self._deadline)
        except BaseException as error:
            # Exceptions must never cross the ctypes callback boundary.
            self._callback_error = error
            return 1

    def _request_cancel(self, signum, frame):
        self._cancel_requested = True

    @staticmethod
    def _check(code):
        if code == 0: return
        primary = code & 255
        name = {5: "source_busy", 6: "source_busy", 9: "query_timeout",
                23: "operation_denied", 8: "operation_denied",
                26: "key_or_format_mismatch", 11: "source_corrupt"}.get(primary, "source_query_failed")
        raise BridgeError(name, native_code=code)

    def _execute(self, sql: str, params: tuple[SqlValue, ...] = (), *, max_rows: int = 200) -> list[Row]:
        if not self.ptr: raise BridgeError("reader_closed")
        if type(max_rows) is not int or not 1 <= max_rows <= 200:
            raise BridgeError("invalid_limit")
        if not isinstance(sql, str) or "\u0000" in sql or len(sql) > 65536:
            raise BridgeError("invalid_query")
        statement, tail = C.c_void_p(), C.c_char_p()
        previous_handler = None
        self._cancel_requested = False
        self._callback_error = None
        if (threading.current_thread() is threading.main_thread()
                and signal.getsignal(signal.SIGINT) is signal.default_int_handler):
            previous_handler = signal.signal(signal.SIGINT, self._request_cancel)
        self._deadline = time.monotonic() + self.query_timeout
        try:
            encoded = sql.encode("utf-8")
            self._check(self.lib.sqlite3_prepare_v2(self.ptr, encoded, len(encoded), C.byref(statement), C.byref(tail)))
            if not statement or (tail.value and tail.value.strip()):
                raise BridgeError("multiple_or_empty_statements")
            if self.lib.sqlite3_bind_parameter_count(statement) != len(params):
                raise BridgeError("invalid_parameters")
            for i, value in enumerate(params, 1):
                if value is None: code = self.lib.sqlite3_bind_null(statement, i)
                elif isinstance(value, int):
                    if not -(1 << 63) <= value < (1 << 63): raise BridgeError("integer_overflow")
                    code = self.lib.sqlite3_bind_int64(statement, i, value)
                elif isinstance(value, float): code = self.lib.sqlite3_bind_double(statement, i, value)
                elif isinstance(value, str):
                    data = value.encode("utf-8")
                    code = self.lib.sqlite3_bind_text(statement, i, data, len(data), C.c_void_p(-1))
                elif isinstance(value, bytes):
                    data = C.create_string_buffer(value)
                    code = self.lib.sqlite3_bind_blob(statement, i, data, len(value), C.c_void_p(-1))
                else: raise BridgeError("invalid_parameters")
                self._check(code)
            rows, total_bytes = [], 0
            while True:
                rc = self.lib.sqlite3_step(statement)
                if rc == 101: return rows
                if rc != 100: self._check(rc)
                if len(rows) >= max_rows: raise BridgeError("result_limit")
                row = {}
                for col in range(self.lib.sqlite3_column_count(statement)):
                    name = self.lib.sqlite3_column_name(statement, col).decode("utf-8")
                    kind = self.lib.sqlite3_column_type(statement, col)
                    if kind == 5: value = None
                    elif kind == 1: value = self.lib.sqlite3_column_int64(statement, col)
                    elif kind == 2: value = self.lib.sqlite3_column_double(statement, col)
                    else:
                        size = self.lib.sqlite3_column_bytes(statement, col)
                        total_bytes += size
                        if total_bytes > 8 * 1024 * 1024: raise BridgeError("result_too_large")
                        fn = self.lib.sqlite3_column_text if kind == 3 else self.lib.sqlite3_column_blob
                        pointer = fn(statement, col)
                        value = C.string_at(pointer, size) if size else b""
                        if kind == 3: value = value.decode("utf-8")
                    row[name] = value
                rows.append(row)
        except UnicodeError:
            raise BridgeError("invalid_text_encoding") from None
        finally:
            if statement: self.lib.sqlite3_finalize(statement)
            self._deadline = float("inf")
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
            if self._callback_error is not None:
                error, self._callback_error = self._callback_error, None
                raise error
            if self._cancel_requested:
                self._cancel_requested = False
                raise KeyboardInterrupt

    def _transaction_control(self, sql):
        self._control = True
        try: self._execute(sql)
        finally: self._control = False

    @contextmanager
    def transaction(self):
        """Open one read transaction, always rolled back on exit; nesting raises nested_transaction."""
        if not self.ptr: raise BridgeError("reader_closed")
        if self._transaction is not None: raise BridgeError("nested_transaction")
        self._transaction_control("BEGIN")
        transaction = self._transaction = ReadTransaction(self)
        try:
            yield transaction
        finally:
            transaction.active = False
            self._transaction = None
            if self.ptr:
                self._transaction_control("ROLLBACK")

    def close(self):
        """Close the connection and deactivate any open transaction; safe to call twice."""
        if self.ptr:
            self.lib.sqlite3_close_v2(self.ptr)
            self.ptr = C.c_void_p()
        if self._transaction is not None:
            self._transaction.active = False

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def open_source(db: SourceDb, raw_key: bytes, *, config: Config,
                profile: str = "sqlcipher4") -> SourceReader:
    """Open a verified read-only reader; raises invalid_cipher_configuration, source_changed or a native error."""
    return SourceReader(db, raw_key, config, profile)
