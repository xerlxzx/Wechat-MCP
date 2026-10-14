"""Verified bounded bytes and safe status, independently of protocol encoding."""
from functools import cached_property

from ..archive.importer import archive_status
from ..attachments.resolver import resolve_attachment
from ..core.config import Config, read_private_json, reject_symlinks
from ..core.errors import BridgeError
from ..core.limits import DEFAULT_RECORDS, MAX_RECORDS, MAX_CONTEXT_NEIGHBORS, MAX_QUERY_CHARACTERS
from ..search.repository import QueryRepository
from ..core.validate import require_int_range
from ..source.keys import load_manifest

DEFAULT_CONTENT_BYTES = 1048576
MAX_CONTENT_BYTES = 8388608


class ToolService:
    def __init__(self, config: Config):
        self.config = config

    @cached_property
    def queries(self):
        return QueryRepository(self.config)

    def _acceptance(self) -> dict:
        root = self.config.state_dir / 'proofs'
        reject_symlinks(root, 'unsafe_state_path')
        paths = []
        for path in root.glob('*.json'):
            paths.append(path)
            if len(paths) > 64:
                return {'status': 'unavailable', 'diagnostic_code': 'proof_inventory_limit'}
        for path in sorted(paths, key=lambda p: p.stat().st_mtime_ns, reverse=True):
            value = read_private_json(path)
            if value.get('proof_scope') == 'live':
                return {'status': value.get('status', 'unavailable'),
                        'expectation_basis': value.get('expectation_basis', 'unknown'),
                        'checks': value.get('checks', {})}
        return {'status': 'not_attested'}

    def status(self) -> dict:
        from ..cli import doctor
        result = archive_status(self.config)
        try:
            count = len(load_manifest(self.config.state_dir / 'keys.json').entries)
            key_error = None
        except BridgeError as error:
            count, key_error = None, error.code
        try:
            acceptance = self._acceptance()
        except (BridgeError, OSError):
            acceptance = {'status': 'unavailable'}
        return result | {'source': 'local_mac_history', 'read_only': True,
                         'environment': doctor(self.config.state_dir),
                         'saved_key_count': count, 'saved_key_error': key_error,
                         'source_acceptance': acceptance,
                         'limits': {'default_records': DEFAULT_RECORDS, 'maximum_records': MAX_RECORDS,
                                    'context_neighbors': MAX_CONTEXT_NEIGHBORS, 'query_characters': MAX_QUERY_CHARACTERS,
                                    'default_content_bytes': DEFAULT_CONTENT_BYTES,
                                    'maximum_content_bytes': MAX_CONTENT_BYTES},
                         'image_decoding': {'supported': ['JPEG', 'PNG', 'WEBP', 'legacy_XOR', 'V1_DAT', 'V2_DAT'],
                                            'unsupported': ['animated', 'WXGF', 'HEVC']}}

    def attachment(self, attachment_id: str, *, representation: str = 'auto', max_bytes: int = DEFAULT_CONTENT_BYTES, generation: int | None = None) -> tuple:
        require_int_range(max_bytes, 1, MAX_CONTENT_BYTES, 'invalid_content_limit')
        if representation not in ('auto', 'source', 'decoded'):
            raise BridgeError('invalid_representation')
        ref, generation = self.queries.attachment_ref(attachment_id, generation=generation)
        content = ('decoded' if ref.kind == 'image' else 'source') if representation == 'auto' else representation
        if content == 'decoded' and ref.kind != 'image':
            raise BridgeError('unsupported_representation')
        with resolve_attachment(ref, self.config, decode=content == 'decoded',
                                max_source_bytes=max_bytes if content == 'source' else None) as result:
            metadata = {'attachment_id': ref.attachment_id, 'message_id': ref.message_id, 'kind': ref.kind,
                        'generation': generation, 'original_filename': ref.original_filename,
                        'availability': result.availability, 'representation': result.representation,
                        'content_representation': content, 'diagnostic_code': result.diagnostic_code,
                        'source_sha256': result.source_sha256, 'decoded_sha256': result.decoded_sha256,
                        'verification_basis': result.verification_basis, 'decoded_available': result.decoded is not None,
                        'mime_type': 'application/octet-stream', 'resource_uri': None}
            if result.handle is None:
                return metadata, None
            if content == 'decoded':
                data = result.decoded.data
                if len(data) > max_bytes:
                    raise BridgeError('content_too_large')
                metadata |= {'mime_type': {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[result.decoded.format],
                             'width': result.decoded.width, 'height': result.decoded.height,
                             'decoder_version': result.decoded.decoder_version}
            else:
                data = result.handle.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise BridgeError('content_too_large')
            metadata |= {'size': len(data), 'resource_uri': f'wechat-attachment://content/{generation}/{attachment_id}/{content}'}
            return metadata, data
