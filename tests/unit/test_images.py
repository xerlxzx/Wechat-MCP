import io
import struct
import pytest
from wechat_bridge.core.errors import BridgeError
from tests.support.images import corrupt_png, dat, png


def test_plain_and_xor_preserve_original_encoded_image_bytes():
    from wechat_bridge.attachments.images import decode_image
    raw=png()
    for encoded in (raw,bytes(v^99 for v in raw)):
        result=decode_image(io.BytesIO(encoded),material=None)
        assert result.data==raw
        assert (result.width,result.height)==(3,2)
        assert result.format=="PNG"


@pytest.mark.parametrize("variant,key",[(b'V1',b'cfcd208495d565ef'),(b'V2',b'0123456789abcdef')])
def test_segmented_dat_validates_lengths_padding_and_pixels(variant,key):
    from wechat_bridge.attachments.images import decode_image,ImageKeyMaterial
    raw=png(); material=ImageKeyMaterial(((key,77),))
    result=decode_image(io.BytesIO(dat(raw,key=key,variant=variant)),material=material)
    assert result.data==raw
    assert (result.width,result.height)==(3,2)


def test_wrong_key_missing_key_and_malformed_dat_are_explicit():
    from wechat_bridge.attachments.images import decode_image,ImageKeyMaterial
    with pytest.raises(BridgeError,match="image_key_unavailable"):
        decode_image(io.BytesIO(dat(png())),material=None)
    with pytest.raises(BridgeError,match="image_key_mismatch"):
        decode_image(io.BytesIO(dat(png())),material=ImageKeyMaterial(((b'x'*16,77),)))
    bad=b'\x07\x08V2\x08\x07'+struct.pack('<II',0xffffffff,3)+b'\0'
    with pytest.raises(BridgeError,match="malformed_image_header"):
        decode_image(io.BytesIO(bad),material=None)


def test_observed_v2_header_marker_one_is_supported():
    from wechat_bridge.attachments.images import decode_image,ImageKeyMaterial
    raw=png();encoded=bytearray(dat(raw));encoded[14]=1
    assert decode_image(io.BytesIO(encoded),material=ImageKeyMaterial(((b'0123456789abcdef',77),))).data==raw


def test_animated_and_wxgf_formats_are_unsupported():
    from wechat_bridge.attachments.images import decode_image
    from PIL import Image
    gif=io.BytesIO(); Image.new('RGB',(2,2)).save(gif,'GIF',save_all=True,append_images=[Image.new('RGB',(2,2),'red')])
    for encoded in (gif.getvalue(),b'wxgf'+b'x'*40):
        with pytest.raises(BridgeError,match="unsupported_image_format"):
            decode_image(io.BytesIO(encoded),material=None)


def test_image_size_limit():
    from wechat_bridge.attachments.images import decode_image
    raw=png()
    with pytest.raises(BridgeError,match="content_too_large"):
        decode_image(io.BytesIO(raw),material=None,max_bytes=20)
    assert decode_image(io.BytesIO(raw),material=None).data==raw


def test_corrupt_png_checksum_is_bounded_diagnostic():
    from wechat_bridge.attachments.images import decode_image
    with pytest.raises(BridgeError,match='malformed_image'):
        decode_image(io.BytesIO(corrupt_png()),material=None)


@pytest.mark.parametrize('mode,size',[('RGBA',(4000,4000)),('RGB',(5000,4000)),('L',(8000,5000)),('I;16',(6000,5000))])
def test_decoded_raster_budget_is_enforced_before_loading(mode,size):
    from PIL import Image
    from wechat_bridge.attachments.images import decode_image
    data=io.BytesIO();Image.new(mode,size).save(data,'PNG')
    # Grayscale 40M bytes fits; RGB/RGBA/16-bit rasters exceed50MiB.
    if mode=='L':
        assert decode_image(io.BytesIO(data.getvalue()),material=None).width==size[0]
    else:
        with pytest.raises(BridgeError,match='decoded_image_too_large'):
            decode_image(io.BytesIO(data.getvalue()),material=None)
