"""Shared ComfyUI process management + HTTP API client, used by both
setup_workflow.py (one-time workflow export) and pipeline.py (the runtime
generation loop)."""

import subprocess
import time
import uuid
from pathlib import Path

import requests


class ComfyUIClient:
    def __init__(self, config: dict, root: Path):
        self.config = config
        self.root = root
        self.base_url = f"http://{config['comfyui_host']}:{config['comfyui_port']}"
        self.client_id = str(uuid.uuid4())
        self._object_info_cache = None

    def ensure_running(self, timeout_s: int = 240):
        if self._is_up():
            return
        print("ComfyUI not running - starting it...")
        comfy_python = self.root / ".venv" / "Scripts" / "python.exe"
        comfy_main = self.root / self.config["comfyui_dir"] / "main.py"
        args = [
            str(comfy_python), str(comfy_main),
            "--listen", self.config["comfyui_host"],
            "--port", str(self.config["comfyui_port"]),
            *self.config.get("comfyui_extra_args", []),
        ]
        subprocess.Popen(args, cwd=str(self.root / self.config["comfyui_dir"]))
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._is_up():
                print("ComfyUI is up.")
                return
            time.sleep(2)
        raise RuntimeError(f"ComfyUI did not come up within {timeout_s}s - check logs above.")

    def _is_up(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/system_stats", timeout=5).ok
        except requests.RequestException:
            return False

    def object_info(self) -> dict:
        if self._object_info_cache is None:
            resp = requests.get(f"{self.base_url}/object_info", timeout=60)
            resp.raise_for_status()
            self._object_info_cache = resp.json()
        return self._object_info_cache

    def submit(self, flat_workflow: dict) -> str:
        # Bounded timeout: without it, a hung ComfyUI server blocks this call
        # forever - run_once() never returns, the worker never polls again,
        # and the job sits "in_progress" with no way to self-recover short of
        # a manual restart.
        try:
            resp = requests.post(
                f"{self.base_url}/prompt",
                json={"prompt": flat_workflow, "client_id": self.client_id},
                timeout=60,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ComfyUI did not respond to prompt submission: {exc}") from exc
        if not resp.ok:
            # ComfyUI's 400 body carries the actual per-node validation
            # errors (e.g. "Value not in list", "Required input is
            # missing") - raise_for_status() discarded that and left only
            # a generic "400 Bad Request", which is useless once this
            # traceback is all that's visible (uploaded to the remote UI,
            # no access to the machine's own console).
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise RuntimeError(f"ComfyUI rejected the prompt ({resp.status_code}): {detail}")
        return resp.json()["prompt_id"]

    def wait_for_result(self, prompt_id: str, timeout_s: int = 1800) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            resp = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=30)
            resp.raise_for_status()
            history = resp.json()
            if prompt_id in history:
                outputs = history[prompt_id]["outputs"]
                for node_output in outputs.values():
                    for key in ("videos", "gifs", "images"):
                        if key in node_output and node_output[key]:
                            return node_output[key][0]
                raise RuntimeError(f"ComfyUI job {prompt_id} finished with no video/image output: {outputs}")
            time.sleep(3)
        raise RuntimeError(f"ComfyUI job {prompt_id} timed out after {timeout_s}s")

    def download_output(self, file_info: dict, output_path: Path):
        params = {
            "filename": file_info["filename"],
            "subfolder": file_info.get("subfolder", ""),
            "type": file_info.get("type", "output"),
        }
        resp = requests.get(f"{self.base_url}/view", params=params, timeout=120)
        resp.raise_for_status()
        output_path.write_bytes(resp.content)
