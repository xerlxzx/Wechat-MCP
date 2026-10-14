"""macOS subprocess boundary. No UI automation, original re-signing or sudo."""
import hashlib
import os
from pathlib import Path
import platform
import signal
import subprocess
import time

from ..core.config import read_private_json, reject_symlinks
from ..core.errors import BridgeError


def command(args, *, timeout=30, env=None):
    """Run a fixed argument list without a shell; any failure raises setup_command_failed."""
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise BridgeError("setup_command_failed") from None
    if result.returncode:
        raise BridgeError("setup_command_failed")
    return result.stdout, result.stderr


def process_identity(pid):
    """Return pid, start time and executable, or None if the process is gone.

    The start time guards against PID reuse. Raises process_inspection_unknown.
    """
    try:
        result = subprocess.run(["/bin/ps", "-p", str(int(pid)), "-o", "lstart=", "-o", "comm="],
                                capture_output=True, text=True, timeout=5)
        if result.returncode or not result.stdout.strip():
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError:
                return None
            except (PermissionError, OSError):
                pass
            raise BridgeError("process_inspection_unknown")
        # lstart is exactly five whitespace-delimited date fields; comm may include spaces.
        values = result.stdout.strip().split(None, 5)
        if len(values) != 6: raise BridgeError("process_inspection_unknown")
        return {"pid": int(pid), "started": " ".join(values[:5]), "executable": values[5]}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise BridgeError("process_inspection_unknown") from None


class SystemBackend:
    """Subprocess operations for setup on macOS; only the signed copy is ever modified."""
    app = Path("/Applications/WeChat.app")

    def preflight(self):
        """Report LLDB and platform readiness (arm64 macOS, not root)."""
        self.lldb_python = command(["/usr/bin/lldb", "-P"])[0].decode().strip()
        env = dict(os.environ, PYTHONPATH=self.lldb_python)
        command(["/usr/bin/python3", "-c", "import lldb"], env=env)
        return {"lldb_ready": True, "platform_ready": platform.system() == "Darwin"
                and platform.machine() == "arm64" and os.geteuid() != 0}

    def signature(self, app):
        """Verify the bundle's signature and return a digest of its codesign details."""
        command(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
        out, err = command(["/usr/bin/codesign", "-dvvv", str(app)])
        return hashlib.sha256(out + err).hexdigest()

    def is_running(self, app):
        """True if any WeChat process runs; an unclear answer raises process_inspection_unknown."""
        try:
            result = subprocess.run(["/usr/bin/pgrep", "-x", "WeChat"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            raise BridgeError("process_inspection_unknown") from None
        # Any running WeChat client blocks launch; two copies share account data.
        if result.returncode == 1: return False
        if result.returncode == 0 and result.stdout.strip(): return True
        raise BridgeError("process_inspection_unknown")

    def copy_bundle(self, app, dest):
        """Copy the bundle to a new path; raises setup_copy_exists."""
        if dest.exists(): raise BridgeError("setup_copy_exists")
        reject_symlinks(dest, "unsafe_state_path")
        command(["/usr/bin/ditto", str(app), str(dest)], timeout=180)

    def sign_copy(self, path):
        """Ad-hoc sign the trial copy only; refuses the installed app (invalid_setup_copy)."""
        if path == self.app or path.name != "WeChat-trial.app": raise BridgeError("invalid_setup_copy")
        command(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(path)], timeout=120)
        command(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(path)], timeout=60)

    def capture(self, plan):
        """Run the LLDB helper against the copy with a 180 s ceiling.

        Raises capture_timeout, capture_process_ended or capture_failed.
        """
        self.preflight()
        helper = Path(__file__).with_name("lldb_capture.py")
        env = dict(os.environ, PYTHONPATH=self.lldb_python)
        args = ["/usr/bin/python3", str(helper), "--exe", str(plan.copy_path / "Contents/MacOS/WeChat"),
                "--db-root", str(plan.config.account_root / "db_storage"),
                "--out", str(plan.directory / "captured.json"), "--owned", str(plan.directory / "owned.json"),
                "--timeout", "150"]
        fd = os.open(plan.directory / "capture.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as log:
            child = subprocess.Popen(args, stdout=log, stderr=log, env=env, start_new_session=True)
            try:
                code = child.wait(timeout=180)
            except (KeyboardInterrupt, subprocess.TimeoutExpired) as error:
                child.send_signal(signal.SIGINT)
                try: child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=5)
                if isinstance(error, KeyboardInterrupt): raise
                raise BridgeError("capture_timeout") from None
        if code != 0:
            status = {3: "capture_timeout", 4: "capture_process_ended"}.get(code, "capture_failed")
            raise BridgeError(status)

    def stop_owned(self, plan):
        """Gracefully stop only the process this plan started; False means not confirmed stopped."""
        try:
            return self._stop_owned(plan)
        except (BridgeError, OSError):
            return False

    def _stop_owned(self, plan):
        owned_path = plan.directory / "owned.json"
        if not owned_path.exists():
            # Never disable/reopen while an unidentified temporary process may exist.
            return not self.is_running(plan.copy_path)
        # Signal only a process whose pid, start time and executable match what this plan recorded.
        owned = read_private_json(owned_path)
        expected_exe = str(plan.copy_path / "Contents/MacOS/WeChat")
        if owned.get("executable") != expected_exe: return False
        current = process_identity(owned["pid"])
        if current is None: return True
        if current != owned: return False
        os.kill(owned["pid"], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = process_identity(owned["pid"])
            if current is None: return True
            if current != owned: return False
            time.sleep(0.1)
        return False

    def disable_copy(self, plan):
        """Rename the trial copy so it cannot launch."""
        if plan.copy_path.exists():
            reject_symlinks(plan.copy_path, "unsafe_state_path")
            plan.copy_path.rename(plan.directory / "WeChat.disabled")

    def launch_original(self, app):
        """Start the installed app detached, restoring the pre-setup state."""
        # Exact executable avoids LaunchServices selecting the signed copy.
        subprocess.Popen([str(app / "Contents/MacOS/WeChat")], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
