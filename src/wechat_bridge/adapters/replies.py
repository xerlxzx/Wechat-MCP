"""Quote content is evidence, never stable identity or an instruction."""
from dataclasses import replace
import re
from .models import Reply
from .messages import parse_xml


def parse_reply(message, payload: bytes) -> Reply | None:
    """Extract the quoted target from a reply payload; None if it is not a reply.

    Raises the parse_xml errors.
    """
    root = parse_xml(payload)
    app = root.find("appmsg")
    if app is None or app.findtext("type") != "57":
        return None
    refer = app.find("refermsg")
    base = Reply(message.message_id, message.source.account_id, message.conversation_id, None)
    if refer is None:
        return replace(base, resolution_status="unsupported", diagnostic_codes=("reply_variant_unsupported",))
    value = refer.findtext("svrid")
    if value is not None and re.fullmatch(r"[0-9]{1,20}", value) and int(value) != 0:
        value = str(int(value))
    else:
        value = None
    quoted = refer.findtext("content") if refer.findtext("type") == "1" else None
    return replace(base, target_server_id=value, quoted_text=quoted,
                   quoted_sender=refer.findtext("displayname") or None,
                   diagnostic_codes=() if value else ("reply_target_missing",))


def resolve_replies(replies, targets):
    """Resolve a target only when exactly one candidate matches; several stay ambiguous."""
    output = []
    for reply in replies:
        if reply.resolution_status == "unsupported":
            output.append(reply); continue
        candidates = tuple(dict.fromkeys(targets.get((reply.account_id, reply.conversation_id, reply.target_server_id), ())))
        status = "unresolved" if not candidates else "resolved" if len(candidates) == 1 else "ambiguous"
        output.append(replace(reply, target_message_id=candidates[0] if len(candidates) == 1 else None, resolution_status=status))
    return tuple(output)
