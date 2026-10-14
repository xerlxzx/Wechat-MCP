import os
import pytest

@pytest.fixture
def source_root(tmp_path):
    root = tmp_path / "accounts" / "synthetic-account"
    for i, relative in enumerate(("contact/contact.db", "session/session.db",
            "message/message_resource.db", "hardlink/hardlink.db",
            "message/message_0.db", "message/message_1.db")):
        path = root / "db_storage" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([i + 1]) * 16 + b"fixture")
    (root / "msg" / "file").mkdir(parents=True)
    return root
