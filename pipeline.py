"""
MiniMax H3 generation loop, sized for a single gaming GPU: drives a local
ComfyUI instance (which handles VRAM offloading) via its HTTP API, rather
than loading the full model straight into one GPU.

workflow_api.json and config.json's prompt_node_id/prompt_input_key are
generated automatically by setup_workflow.py (run once by install.ps1) -
no manual export needed.

Remote UI contract (see config.example.json) is a placeholder until a real
service exists:
  GET  {base}{fetch_prompt_endpoint}      -> 200 {"job_id": str, "prompt": str}
                                              or 204 (no job queued)
  POST {base}{upload_endpoint w/ job_id}  -> multipart file upload of the video
"""

import argparse
import copy
import json
import socket
import sys
import time
import traceback
from pathlib import Path

import requests

from comfy_client import ComfyUIClient

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

RETRYABLE_ATTEMPTS = 3
RETRYABLE_BACKOFF_SECONDS = 5


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit("config.json not found - run install.ps1 first, or copy config.example.json.")
    return json.loads(CONFIG_PATH.read_text())


def _request_headers(config: dict) -> dict:
    return {"X-Worker-Id": config.get("worker_id") or socket.gethostname()}


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Retries transient failures (network errors, 5xx) with backoff. Does not
    retry 4xx - those won't fix themselves."""
    last_exc = None
    for attempt in range(1, RETRYABLE_ATTEMPTS + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code < 500:
                return resp
            last_exc = RuntimeError(f"{method} {url} -> HTTP {resp.status_code}")
        except requests.RequestException as exc:
            last_exc = exc
        if attempt < RETRYABLE_ATTEMPTS:
            print(f"  request failed ({last_exc}), retrying in {RETRYABLE_BACKOFF_SECONDS}s "
                  f"[{attempt}/{RETRYABLE_ATTEMPTS}]...")
            time.sleep(RETRYABLE_BACKOFF_SECONDS)
    raise last_exc


def fetch_next_prompt(config: dict) -> dict | None:
    url = config["remote_ui_base_url"].rstrip("/") + config["fetch_prompt_endpoint"]
    resp = _request_with_retry("GET", url, headers=_request_headers(config), timeout=30)
    if resp.status_code == 204:
        return None
    resp.raise_for_status()
    return resp.json()


def upload_result(config: dict, job_id: str, video_path: Path) -> None:
    endpoint = config["upload_endpoint"].replace("{job_id}", job_id)
    url = config["remote_ui_base_url"].rstrip("/") + endpoint
    with open(video_path, "rb") as f:
        resp = _request_with_retry(
            "POST", url, files={"video": (video_path.name, f, "video/mp4")},
            headers=_request_headers(config), timeout=300,
        )
    resp.raise_for_status()


def report_failure(config: dict, job_id: str, error: str) -> None:
    """Best-effort: tells the remote UI this job failed so it shows up as
    'failed' instead of stuck 'in_progress'. If this itself fails (remote UI
    unreachable), the server's own stale-claim timeout will eventually
    re-queue the job anyway - so don't let a failure here crash the loop."""
    try:
        endpoint = config.get("fail_endpoint", "/jobs/{job_id}/fail").replace("{job_id}", job_id)
        url = config["remote_ui_base_url"].rstrip("/") + endpoint
        requests.post(url, json={"error": error}, headers=_request_headers(config), timeout=30)
    except requests.RequestException as exc:
        print(f"  (could not report failure to remote UI: {exc})")


class Generator:
    def __init__(self, config: dict, client: ComfyUIClient):
        self.config = config
        self.client = client

        workflow_path = ROOT / config["workflow_path"]
        if not workflow_path.exists():
            sys.exit(
                f"{workflow_path} not found. Run: .venv\\Scripts\\python setup_workflow.py "
                "to generate it (install.ps1 does this automatically)."
            )
        self.workflow_template = json.loads(workflow_path.read_text())

        if config.get("prompt_node_id") in (None, "", "REPLACE_ME"):
            sys.exit(
                "config.json: prompt_node_id is not set. Run setup_workflow.py to "
                "auto-detect it (install.ps1 does this automatically)."
            )

    def generate(self, prompt: str, output_path: Path) -> Path:
        workflow = copy.deepcopy(self.workflow_template)
        node_id = self.config["prompt_node_id"]
        input_key = self.config["prompt_input_key"]
        workflow[node_id]["inputs"][input_key] = prompt

        prompt_id = self.client.submit(workflow)
        file_info = self.client.wait_for_result(prompt_id)
        self.client.download_output(file_info, output_path)
        return output_path


def run_once(config: dict, generator: Generator, output_dir: Path, override_prompt: str | None) -> bool:
    if override_prompt is not None:
        job = {"job_id": "local-test", "prompt": override_prompt}
        upload = False
    else:
        job = fetch_next_prompt(config)
        upload = True
        if job is None:
            return False

    print(f"Job {job['job_id']}: {job['prompt']!r}")
    try:
        # Self-heal: if ComfyUI died since the last job, restart it here rather
        # than letting this job (and every job after it) fail.
        generator.client.ensure_running()

        output_path = output_dir / f"{job['job_id']}.mp4"
        generator.generate(job["prompt"], output_path)
        print(f"Saved {output_path}")

        if upload:
            upload_result(config, job["job_id"], output_path)
            print("Uploaded result to remote UI")
    except Exception:
        error = traceback.format_exc()
        print(f"Job {job['job_id']} failed:\n{error}")
        if upload:
            report_failure(config, job["job_id"], error)
        # Don't re-raise: one bad prompt/OOM/transient error shouldn't take
        # down the whole worker. Move on to the next poll cycle.

    return True


def main():
    parser = argparse.ArgumentParser(description="MiniMax H3 fetch-generate-upload pipeline (single-GPU, via ComfyUI)")
    parser.add_argument("--once", action="store_true", help="run a single poll-generate-upload cycle then exit")
    parser.add_argument("--prompt", help="skip remote fetch, generate this prompt locally, no upload")
    args = parser.parse_args()

    config = load_config()
    output_dir = ROOT / config["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    client = ComfyUIClient(config, ROOT)
    for attempt in range(1, 4):
        try:
            client.ensure_running()
            break
        except RuntimeError as exc:
            if attempt == 3:
                raise  # let the process exit non-zero - the scheduled task's watchdog trigger will retry it
            print(f"ComfyUI failed to start (attempt {attempt}/3): {exc}\nRetrying in 30s...")
            time.sleep(30)
    generator = Generator(config, client)

    if args.prompt:
        run_once(config, generator, output_dir, args.prompt)
        return

    if args.once:
        did_work = run_once(config, generator, output_dir, None)
        if not did_work:
            print("No job queued.")
        return

    print("Polling remote UI. Ctrl+C to stop.")
    while True:
        try:
            did_work = run_once(config, generator, output_dir, None)
            if not did_work:
                time.sleep(config["poll_interval_seconds"])
        except KeyboardInterrupt:
            raise
        except Exception:
            # Last-resort net: fetch_next_prompt() itself can still raise (e.g.
            # remote UI unreachable past the retry budget). Log and keep the
            # worker alive rather than exiting - the next poll may well succeed.
            print(f"Unexpected error in poll loop, will retry:\n{traceback.format_exc()}")
            time.sleep(config["poll_interval_seconds"])


if __name__ == "__main__":
    main()
