"""Explicit source acceptance with private expectations and redacted reports."""
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import platform
from pathlib import Path
import re
import uuid

from .adapters.models import SourceRef
from .adapters.messages import decode_message
from .adapters.wechat_4_1 import open_adapter
from .attachments.images import DECODER_VERSION
from .attachments.resolver import resolve_attachment
from .core.config import read_private_json, write_private_json
from .core.errors import BridgeError

SAMPLES = ("message", "contact", "group", "reply", "original_image", "ordinary_file")
REQUIRED = {"message": ("text", "sender_username", "utc_timestamp"),
            "contact": ("source_username", "display_name"),
            "group": ("source_username", "member_usernames"),
            "reply": ("target_server_id", "resolution_status", "quoted_text"),
            "original_image": ("decoded_sha256", "width", "height"),
            "ordinary_file": ("source_sha256", "size")}


@dataclass(frozen=True)
class ProofSelection:
    """Private sample choices and expected values; the basis says who confirmed them."""
    samples: dict = field(repr=False)
    expectation_basis: str = "pending_human_confirmation"
    build: str | None = None

    def __post_init__(self):
        if (not isinstance(self.samples, dict) or len(self.samples) > len(SAMPLES)
                or any(k not in SAMPLES for k in self.samples)
                or self.expectation_basis not in ("synthetic_fixture", "user_confirmed", "pending_human_confirmation")):
            raise BridgeError("invalid_proof_selection")

    def private_dict(self):
        return {"version": 1, "samples": self.samples, "expectation_basis": self.expectation_basis, "build": self.build}


def load_selection(path):
    """Load a private selection file; synthetic fixtures are refused here (synthetic_selection_not_allowed)."""
    data = read_private_json(path)
    try:
        if data["version"] != 1:
            raise ValueError
        selection = ProofSelection(data["samples"], data.get("expectation_basis", "pending_human_confirmation"), data.get("build"))
        if selection.expectation_basis == "synthetic_fixture":
            raise BridgeError("synthetic_selection_not_allowed")
        return selection
    except (KeyError, TypeError, ValueError):
        raise BridgeError("invalid_proof_selection") from None


@dataclass(frozen=True)
class ProofCheck:
    """One check outcome: passed, failed or blocked, with diagnostic codes; evidence stays private."""
    status: str
    diagnostic_codes: tuple[str, ...] = ()
    evidence: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ProofReport:
    """Overall proof result. Only public_dict() is safe to print; the evidence file is private."""
    status: str
    checks: dict[str, ProofCheck]
    versions: dict
    coverage: dict
    observations: dict = field(repr=False)
    evidence_path: Path = field(repr=False)
    proof_scope: str = "live"
    expectation_basis: str = "pending_human_confirmation"

    def public_dict(self):
        """Statuses, codes, versions and aggregate coverage; no sample content."""
        return {"status": self.status, "checks": {name: {"status": check.status, "diagnostic_codes": list(check.diagnostic_codes)} for name, check in self.checks.items()},
                "versions": self.versions, "coverage": self.coverage,
                "proof_scope": self.proof_scope, "expectation_basis": self.expectation_basis}


def read_selected_message(adapter, selector):
    """Read exactly one selected message row by its pinned database, table and local ID.

    Raises invalid_proof_selection, selected_sample_missing, selected_source_missing_or_replaced
    or selected_identity_conflicting.
    """
    try:
        selected = selector["source"]
        local = selected["local_id"]
        if not isinstance(local, str) or not re.fullmatch(r"-?[0-9]{1,19}", local) or not -(1 << 63) <= int(local) < (1 << 63):
            raise BridgeError("invalid_proof_selection")
        dbid, table = selected["database_id"], selected["table"]
        conv = next((c for c in adapter.conversations() if c.conversation_id == selector["conversation_id"]), None)
        if conv is None:
            raise BridgeError("selected_sample_missing")
        mapping = [(s, t) for s, t in adapter._message_tables(conv.source_username) if s.database_id == dbid and t == table]
        if len(mapping) != 1:
            raise BridgeError("selected_source_missing_or_replaced")
        schema = mapping[0][0]
        columns = "local_id,server_id,local_type,real_sender_id,create_time,CAST(message_content AS BLOB) message_content,WCDB_CT_message_content"
        if "packed_info_data" in dict(schema.table(table).columns):
            columns += ",packed_info_data"
        adapter.identities.add_conversation(dbid, table, conv.source_username)
        with adapter._open(schema.relative_path) as reader, reader.transaction() as tx:
            rows = tx.query("SELECT " + columns + " FROM " + schema.quote_table(table) + " WHERE local_id=? LIMIT 2", (int(local),), max_rows=2)
            if not rows:
                raise BridgeError("selected_sample_missing")
            if len(rows) != 1:
                raise BridgeError("selected_identity_conflicting")
            row = rows[0]; sender = row["real_sender_id"]
            adapter.identities.clear_sender(dbid, sender)
            senders = tx.query("SELECT rowid,user_name FROM Name2Id WHERE rowid=?", (sender,), max_rows=2)
            for record in senders:
                value = record["user_name"]
                if isinstance(value, str) and value and "\0" not in value:
                    adapter.identities.add_sender(dbid, sender, value)
            observed = datetime.now(UTC).isoformat()
            adapter.observations[dbid] = observed
            source = SourceRef(reader.db.account_id, dbid, table, str(row["local_id"]), str(row["server_id"]) if row["server_id"] else None, observed)
        message = decode_message(row | {"timestamp_unit": "seconds"}, source, adapter.identities)
        if message.reply:
            from dataclasses import replace
            message = replace(message, reply=adapter._resolve_reply(message.reply, conv.source_username))
        return message
    except (KeyError, TypeError, ValueError):
        raise BridgeError("invalid_proof_selection") from None


def _valid_expected(name, expected):
    def text(key, *, nullable=False, nonempty=False):
        value = expected[key]
        return (nullable and value is None) or isinstance(value, str) and (not nonempty or bool(value))

    def digest(key):
        return isinstance(expected[key], str) and bool(re.fullmatch(r"[0-9a-f]{64}", expected[key]))

    if name == "message":
        if not text("text", nonempty=True) or not text("sender_username", nonempty=True) or not text("utc_timestamp", nonempty=True):
            return False
        try:
            stamp = datetime.fromisoformat(expected["utc_timestamp"])
            return stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0)
        except ValueError:
            return False
    if name == "contact":
        return text("source_username", nonempty=True) and text("display_name", nullable=True)
    if name == "group":
        names = expected["member_usernames"]
        return text("source_username", nonempty=True) and isinstance(names, list) and 1 <= len(names) <= 200 and all(isinstance(s, str) and s for s in names)
    if name == "reply":
        server = expected["target_server_id"]
        return isinstance(server, str) and bool(re.fullmatch(r"[0-9]{1,20}", server)) and int(server) != 0 and expected["resolution_status"] == "resolved" and text("quoted_text", nullable=True)
    if name == "original_image":
        return digest("decoded_sha256") and all(type(expected[k]) is int and expected[k] > 0 for k in ("width", "height"))
    return digest("source_sha256") and type(expected["size"]) is int and expected["size"] >= 0


def _check_sample(adapter, name, sample):
    expected = sample.get("expected", {})
    if not isinstance(expected, dict) or any(key not in expected for key in REQUIRED[name]):
        return ProofCheck("blocked", ("expected_values_missing",))
    if name not in ("original_image", "ordinary_file") and not _valid_expected(name, expected):
        return ProofCheck("blocked", ("expected_values_invalid",))
    if name == "message":
        expected = expected | {"utc_timestamp": datetime.fromisoformat(expected["utc_timestamp"]).isoformat()}
    if name == "contact":
        value = next((c for c in adapter.contacts() if c.contact_id == sample.get("contact_id")), None)
        if value is None:
            return ProofCheck("blocked", ("selected_sample_missing",))
        observed = {"source_username": value.source_username, "display_name": value.display_name}
        evidence = asdict(value)
    elif name == "group":
        value = next((c for c in adapter.conversations() if c.conversation_id == sample.get("conversation_id") and c.kind == "group"), None)
        if value is None or value.membership_status != "recorded":
            return ProofCheck("blocked", ("membership_unsupported",))
        members = tuple(adapter.memberships(value.conversation_id))
        if any(m.contact_id is None or m.parse_status != "ok" for m in members):
            return ProofCheck("blocked", ("membership_partial",))
        names = {c.contact_id: c.source_username for c in adapter.contacts()}
        observed = {"source_username": value.source_username, "member_usernames": sorted(names[m.contact_id] for m in members)}
        expected = expected | {"member_usernames": sorted(expected["member_usernames"])}
        evidence = {"conversation": asdict(value), "memberships": [asdict(m) for m in members]}
    else:
        value = read_selected_message(adapter, sample)
        if value.utc_timestamp is None:
            return ProofCheck("blocked", ("invalid_timestamp",))
        if value.parse_status in ("malformed", "unsupported"):
            return ProofCheck("blocked", value.diagnostic_codes)
        evidence = {"message": asdict(value)}
        if name == "message":
            if value.type != "text" or value.sender_id is None:
                return ProofCheck("blocked", ("selected_message_or_sender_unsupported",))
            expected_sender = adapter.identities.contact_id(expected["sender_username"])
            observed = {"text": value.text, "sender_username": expected["sender_username"] if expected_sender == value.sender_id else None, "utc_timestamp": value.utc_timestamp}
        elif name == "reply":
            if value.reply is None:
                return ProofCheck("blocked", ("selected_reply_missing",))
            if value.reply.resolution_status != "resolved":
                return ProofCheck("blocked", ("selected_reply_" + value.reply.resolution_status,))
            observed = {"target_server_id": value.reply.target_server_id, "resolution_status": value.reply.resolution_status, "quoted_text": value.reply.quoted_text}
        else:
            refs = tuple(r for r in adapter.attachment_refs(value) if r.representation == "original" and (not sample.get("attachment_id") or r.attachment_id == sample["attachment_id"]))
            if len(refs) != 1:
                return ProofCheck("blocked" if not refs else "failed", ("selected_attachment_missing_or_conflicting",))
            with resolve_attachment(refs[0], adapter.config) as result:
                evidence["attachment"] = {"ref": asdict(refs[0]), "availability": result.availability,
                                          "source_sha256": result.source_sha256, "decoded_sha256": result.decoded_sha256, "verification_basis": result.verification_basis}
                if result.availability != "original_available":
                    return ProofCheck("failed" if result.availability == "identity_mismatch" else "blocked", (result.diagnostic_code or result.availability,), evidence)
                if not _valid_expected(name, expected):
                    return ProofCheck("blocked", ("expected_values_invalid",), evidence)
                if name == "original_image":
                    if refs[0].kind != "image" or result.decoded is None:
                        return ProofCheck("failed", ("selected_attachment_kind_mismatch",), evidence)
                    observed = {"decoded_sha256": result.decoded_sha256, "width": result.decoded.width, "height": result.decoded.height}
                else:
                    if refs[0].kind != "file":
                        return ProofCheck("failed", ("selected_attachment_kind_mismatch",), evidence)
                    digest = hashlib.sha256(); size = 0
                    while chunk := result.handle.read(65536):
                        digest.update(chunk); size += len(chunk)
                    if digest.hexdigest() != result.source_sha256:
                        return ProofCheck("failed", ("attachment_identity_mismatch",), evidence)
                    observed = {"source_sha256": digest.hexdigest(), "size": size}
    mismatch = any(observed[k] != expected[k] for k in REQUIRED[name])
    return ProofCheck("failed" if mismatch else "passed", ("expected_values_mismatch",) if mismatch else (), evidence)


def run_proof(config, selection: ProofSelection) -> ProofReport:
    """Compare selected samples with expected values using the production adapter and resolver.

    Unconfirmed expectations never pass: they stay blocked. Writes a private
    evidence file under proofs/. Source self-consistency is not ground truth.
    """
    checks = {name: ProofCheck("blocked", ("source_not_readable",)) for name in ("key_coverage", "schema", "ground_truth", *SAMPLES)}
    versions = {"python": platform.python_version(), "decoder": DECODER_VERSION}
    coverage = {}; observations = {}; schemas = []
    try:
        synthetic = selection.expectation_basis == "synthetic_fixture"
        # Only the in-process synthetic fixture interface can inject its build.
        # Production JSON is rejected above and live proofs inspect the app.
        adapter = open_adapter(config, build=selection.build if synthetic else None)
        if not synthetic and selection.build is not None and selection.build != adapter.build:
            raise BridgeError("unsupported_build")
        versions |= {"wechat": "4.1.15", "build": adapter.build, "adapter": adapter.version}
        with adapter._open("contact/contact.db") as reader:
            versions["sqlcipher"] = reader.cipher_version
            versions["sqlite"] = reader.lib.sqlite3_libversion().decode()
        full_coverage = adapter.coverage.public_dict()
        coverage = {k: full_coverage[k] for k in ("ready", "discovered_count", "required_count", "verified_count", "verification_scope", "full_page_integrity_checked", "issues")}
        checks["key_coverage"] = ProofCheck("passed")
        checks["schema"] = ProofCheck("passed" if adapter.parse_status == "ok" else "blocked", ("schema_partial",) if adapter.parse_status != "ok" else ())
        confirmed = selection.expectation_basis in ("user_confirmed", "synthetic_fixture")
        checks["ground_truth"] = ProofCheck("passed" if confirmed else "blocked", () if confirmed else ("expectations_unconfirmed",))
        for name in SAMPLES:
            sample = selection.samples.get(name)
            try:
                checks[name] = _check_sample(adapter, name, sample) if isinstance(sample, dict) else ProofCheck("blocked", ("selected_sample_missing",))
            except BridgeError as error:
                failed = error.code in ("selected_identity_conflicting", "attachment_identity_mismatch")
                checks[name] = ProofCheck("failed" if failed else "blocked", (error.code,))
            except (TypeError, KeyError, ValueError):
                checks[name] = ProofCheck("blocked", ("invalid_proof_selection",))
            if not confirmed and checks[name].status == "passed":
                checks[name] = ProofCheck("blocked", ("expectations_unconfirmed",), checks[name].evidence)
        observations = adapter.observations
        schemas = [s.private_dict() for s in adapter.schemas]
    except BridgeError as error:
        target = "schema" if error.code in ("unsupported_build", "unsupported_schema") else "key_coverage"
        checks[target] = ProofCheck("blocked", (error.code,))
    status = "failed" if any(c.status == "failed" for c in checks.values()) else "passed" if all(c.status == "passed" for c in checks.values()) else "blocked"
    path = config.state_dir / "proofs" / (str(uuid.uuid4()) + ".json")
    report = ProofReport(status, checks, versions, coverage, observations, path,
                         "synthetic" if selection.expectation_basis == "synthetic_fixture" else "live", selection.expectation_basis)
    write_private_json(path, report.public_dict() | {"selection": selection.private_dict(), "observations": observations,
                       "schemas": schemas, "evidence": {name: check.evidence for name, check in checks.items()},
                       "source_write_rejection_basis": "encrypted_fixture_tests_same_production_reader",
                       "observed_at": datetime.now(UTC).isoformat()})
    return report
