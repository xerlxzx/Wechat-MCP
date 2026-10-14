"""Filesystem hints must yield independently validated image bytes, never DB keys."""
import io

import pytest

from wechat_bridge.core.config import Config, write_private_json
from wechat_bridge.core.errors import BridgeError
from tests.support.images import dat, png


def media_case(tmp_path):
    root = tmp_path.resolve() / 'Data/Documents/xwechat_files/wxid_fictional_ab12'
    root.mkdir(parents=True)
    return Config(tmp_path.resolve() / 'state', root)


@pytest.mark.parametrize('relative', [
    'Documents/app_data/net/kvcomm',
    'Documents/app_data/ilink/kvcomm',
    'Documents/xwechat/net/kvcomm',
    'Library/Application Support/com.tencent.xinWeChat/xwechat/net/kvcomm',
    'Library/Application Support/com.tencent.xinWeChat/net/kvcomm',
])
def test_media_hint_in_selected_container_decodes_complete_image(tmp_path, relative):
    from wechat_bridge.attachments.images import load_image_material, decode_image
    cfg = media_case(tmp_path)
    hint = tmp_path.resolve() / 'Data' / relative / 'key_12345_fixture.statistic'
    hint.parent.mkdir(parents=True)
    hint.touch()
    # Literal independently calculated MD5 prefix for 12345 + wxid_fictional.
    raw = png()
    encoded = dat(raw, key=b'74cd0db40ee9456b', xor=57)
    result = decode_image(io.BytesIO(encoded), material=load_image_material(cfg))
    assert result.data == raw
    assert (result.width, result.height) == (3, 2)


def test_missing_and_unrelated_hints_cannot_supply_media_material(tmp_path):
    from wechat_bridge.attachments.images import load_image_material
    cfg = media_case(tmp_path)
    other = tmp_path.resolve() / 'other-container/Documents/app_data/net/kvcomm'
    other.mkdir(parents=True)
    (other / 'key_12345_fixture.statistic').touch()
    assert load_image_material(cfg) is None


def test_all_known_locations_are_checked_even_when_first_is_empty(tmp_path):
    from wechat_bridge.attachments.images import load_image_material, decode_image
    cfg = media_case(tmp_path)
    (tmp_path.resolve() / 'Data/Documents/app_data/net/kvcomm').mkdir(parents=True)
    later = tmp_path.resolve() / 'Data/Documents/xwechat/net/kvcomm'
    later.mkdir(parents=True)
    (later / 'key_12345_fixture.statistic').touch()
    assert decode_image(io.BytesIO(dat(png(), key=b'74cd0db40ee9456b', xor=57)),
                        material=load_image_material(cfg)).data == png()


def test_media_material_directory_symlink_is_rejected(tmp_path):
    from wechat_bridge.attachments.images import load_image_material
    cfg = media_case(tmp_path)
    outside = tmp_path.resolve() / 'outside'
    outside.mkdir()
    (outside / 'key_12345_fixture.statistic').touch()
    link = tmp_path.resolve() / 'Data/Documents/xwechat/net/kvcomm'
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(BridgeError, match='media_material_symlink'):
        load_image_material(cfg)


def test_media_hint_directory_scanning_is_bounded(tmp_path):
    from wechat_bridge.attachments.images import load_image_material
    cfg = media_case(tmp_path)
    root = tmp_path.resolve() / 'Data/Documents/app_data/net/kvcomm'
    root.mkdir(parents=True)
    for index in range(4097):
        (root / f'unrelated-{index}').touch()
    with pytest.raises(BridgeError, match='image_material_limit'):
        load_image_material(cfg)


def test_private_explicit_material_remains_authoritative(tmp_path):
    from wechat_bridge.attachments.images import load_image_material, decode_image
    cfg = media_case(tmp_path)
    write_private_json(cfg.state_dir / 'image-material.json', {
        'version': 1, 'candidates': [{'aes_hex': b'0123456789abcdef'.hex(), 'xor': 77}],
    })
    assert decode_image(io.BytesIO(dat(png())), material=load_image_material(cfg)).data == png()
