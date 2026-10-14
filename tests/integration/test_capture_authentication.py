from tests.support.encrypted import create_database, KEY


def test_captured_key_authenticates_native_encrypted_page(tmp_path):
    from wechat_bridge.setup.lldb_capture import verified
    path = tmp_path / "sample.db"
    create_database(path, wal=False).close()
    page = path.read_bytes()[:4096]
    assert verified(KEY, page)
    assert not verified(b"z" * 32, page)
    assert not verified(KEY, page[:100])
    changed = bytearray(page); changed[100] ^= 1
    assert not verified(KEY, bytes(changed))
