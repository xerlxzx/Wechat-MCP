"""Attachment reference and resolution records; paths and digests stay out of repr."""
from dataclasses import dataclass, field
from typing import BinaryIO


@dataclass(frozen=True)
class AttachmentRef:
    """Archived pointer to one attachment: mapping provenance plus the expected size, digest and file identity."""
    attachment_id: str
    message_id: str
    kind: str
    relative_path: str | None = field(repr=False)
    original_filename: str | None = field(default=None, repr=False)
    expected_size: int | None = None
    expected_digest: str | None = field(default=None, repr=False)
    representation: str = "original"
    mapping_provenance: str = "unsupported"
    file_identity: tuple[int, int, int, int, int] | None = field(default=None, repr=False)
    declared_local: bool | None = None
    diagnostic_code: str | None = None


@dataclass
class AttachmentResult:
    """Outcome of resolving a reference. Availability is explicit; a thumbnail never reports original_available."""
    attachment_id: str
    availability: str
    representation: str
    source_sha256: str | None = field(default=None, repr=False)
    decoded_sha256: str | None = field(default=None, repr=False)
    verification_basis: str | None = None
    diagnostic_code: str | None = None
    handle: BinaryIO | None = field(default=None, repr=False)
    decoded: object | None = field(default=None, repr=False)

    def close(self):
        """Close the verified handle, if any."""
        if self.handle is not None:
            self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
