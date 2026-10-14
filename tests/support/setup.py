"""Controlled external-app backend; filesystem journal and validation stay real."""
from pathlib import Path
import shutil
import time
from wechat_bridge.core.config import write_private_json
from wechat_bridge.core.errors import BridgeError

class FakeBackend:
    def __init__(self, tmp_path):
        self.app = tmp_path / "WeChat.app"
        (self.app / "Contents/MacOS").mkdir(parents=True)
        (self.app / "Contents/MacOS/WeChat").write_bytes(b"installed-app")
        self.signature_value = "original-signature"
        self.running = True
        self.launched = 0
        self.signed = []
        self.fail = None
        self.candidates = {}
        self.owned_identity = None
        self.current_identity = None
        self.stopped = []

    def signature(self, app): return self.signature_value
    def preflight(self): return {"lldb_ready": True, "platform_ready": True}
    def is_running(self, app): return self.running
    def copy_bundle(self, app, dest): shutil.copytree(app, dest)
    def sign_copy(self, path):
        self.signed.append(path)
        if self.fail == "sign_failed": raise BridgeError(self.fail)
        (path / "Contents/MacOS/WeChat").write_bytes(b"signed-copy")
    def capture(self, plan):
        if self.fail == "cancel": raise KeyboardInterrupt
        if self.fail: raise BridgeError(self.fail)
        write_private_json(plan.directory / "captured.json", {"keys": self.candidates})
    def stop_owned(self, plan):
        if self.current_identity is not None:
            if self.current_identity != self.owned_identity:
                return False
            self.stopped.append(self.current_identity)
            self.current_identity = None
        return True
    def disable_copy(self, plan):
        if plan.copy_path.exists(): plan.copy_path.rename(plan.directory / "WeChat.disabled")
    def launch_original(self, app): self.launched += 1; self.running = True
