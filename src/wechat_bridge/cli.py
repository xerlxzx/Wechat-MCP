"""Small explicit local commands; diagnostics are redacted JSON."""
import argparse
import json
import os
import sys
from pathlib import Path
import platform
import plistlib

from .core.config import DEFAULT_STATE, DEFAULT_LIBRARY, create_config, load_config
from .core.errors import BridgeError
from .core.limits import DEFAULT_RECORDS
from .source.discovery import discover


def doctor(state_dir: Path) -> dict:
    result = {"python_version": platform.python_version(), "architecture": platform.machine(),
              "sqlcipher_available": DEFAULT_LIBRARY.is_file(),
              "configured": (state_dir / "config.json").is_file()}
    try:
        with Path("/Applications/WeChat.app/Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        result["wechat_version"] = info.get("CFBundleShortVersionString")
        result["wechat_build"] = info.get("CFBundleVersion")
    except (OSError, plistlib.InvalidFileException):
        result["wechat_version"] = None
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wechat-bridge")
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("WECHAT_BRIDGE_STATE_DIR", DEFAULT_STATE)))
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("doctor")
    subs.add_parser("discover")
    subs.add_parser("inspect-schema")
    subs.add_parser("archive-refresh")
    subs.add_parser("archive-status")
    subs.add_parser("search-index")
    search = subs.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=DEFAULT_RECORDS)
    serve = subs.add_parser("serve")
    serve.add_argument("--no-watch", action="store_true")
    watch = subs.add_parser("archive-watch")
    watch.add_argument("--debounce-seconds", type=float, default=1.0)
    watch.add_argument("--reconcile-seconds", type=float, default=60.0)
    prove = subs.add_parser("prove")
    prove.add_argument("--selection", type=Path, required=True)
    verify = subs.add_parser("verify-keys")
    verify.add_argument("--manifest", type=Path)
    setup = subs.add_parser("setup")
    setup_subs = setup.add_subparsers(dest="setup_command", required=True)
    setup_subs.add_parser("prepare")
    for name in ("run", "recover"):
        setup_subs.add_parser(name).add_argument("--plan-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.state_dir)
        elif args.command == "serve":
            from .mcp.server import serve
            serve(load_config(args.state_dir), watch=not args.no_watch)
            return 0
        elif args.command == "search-index":
            from .archive.store import Archive
            from .archive.importer import import_lock
            from .search.index import ensure_index
            cfg = load_config(args.state_dir)
            archive = Archive(cfg)
            with import_lock(cfg):
                ensure_index(archive)
            result = {"status": "indexed", "active_generation": archive.status()["active_generation"]}
        elif args.command == "search":
            from .search.repository import QueryRepository
            result = QueryRepository(load_config(args.state_dir)).search_messages(args.query, limit=args.limit)
        elif args.command == "archive-refresh":
            from .archive.importer import refresh_archive
            result = refresh_archive(load_config(args.state_dir))
            print(json.dumps(result))
            return 0 if result["status"] == "complete" else 2
        elif args.command == "archive-status":
            from .archive.importer import archive_status
            result = archive_status(load_config(args.state_dir))
            print(json.dumps(result))
            return 0 if result["ready_for_local_queries"] else 2
        elif args.command == "archive-watch":
            import threading
            from .archive.watcher import ArchiveWatcher
            watcher = ArchiveWatcher(load_config(args.state_dir), debounce_seconds=args.debounce_seconds,
                                     reconcile_seconds=args.reconcile_seconds)
            stop = threading.Event()
            try:
                watcher.run(stop)
            finally:
                stop.set()
            return 0
        elif args.command == "inspect-schema":
            from .adapters.wechat_4_1 import open_adapter
            result = open_adapter(load_config(args.state_dir)).public_dict()
        elif args.command == "prove":
            from .proof import load_selection, run_proof
            report = run_proof(load_config(args.state_dir), load_selection(args.selection))
            print(json.dumps(report.public_dict()))
            return {"passed": 0, "blocked": 2, "failed": 1}[report.status]
        elif args.command == "setup":
            from .setup.prepare import prepare_setup, load_setup
            cfg = load_config(args.state_dir)
            if args.setup_command == "prepare":
                result = prepare_setup(cfg, discover(cfg)).public_dict()
            else:
                plan = load_setup(cfg, args.plan_id)
                if args.setup_command == "run":
                    from .setup.capture import run_capture
                    outcome = run_capture(plan)
                    result = {"status": outcome.status, "recovery_status": outcome.recovery_status,
                              "key_copies_removed": outcome.key_copies_removed}
                else:
                    from .setup.recovery import recover_setup
                    outcome = recover_setup(plan)
                    result = {"status": outcome.status, "key_copies_removed": outcome.key_copies_removed}
                print(json.dumps(result))
                return 0 if result["status"] in ("verified", "recovered") and result.get("recovery_status", "recovered") == "recovered" else 2
        elif args.command == "verify-keys":
            from .source.keys import load_manifest, verify_keys, publish_verified_manifest
            cfg = load_config(args.state_dir)
            manifest_path = args.manifest or cfg.state_dir / "keys.json"
            if not manifest_path.exists():
                raise BridgeError("setup_required")
            manifest = load_manifest(manifest_path)
            coverage = verify_keys(discover(cfg), manifest, cfg)
            publish_verified_manifest(manifest, coverage, cfg)
            print(json.dumps(coverage.public_dict()))
            return 0 if coverage.ready else 2
        else:
            result = discover(create_config(args.state_dir)).public_dict()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except BridgeError as error:
        print(json.dumps(error.public_dict()), file=sys.stderr if args.command == 'serve' else sys.stdout)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error": "operation_cancelled"}), file=sys.stderr if args.command == 'serve' else sys.stdout)
        return 130
    except Exception:
        print(json.dumps({"error": "internal_error"}), file=sys.stderr if args.command == 'serve' else sys.stdout)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
