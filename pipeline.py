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
import sys
import time
from pathlib import Path

import requests

from comfy_client import ComfyUIClient

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit("config.json not found - run install.ps1 first, or copy config.example.json.")
    return json.loads(CONFIG_PATH.read_text())


def _auth_headers(config: dict) -> dict:
    api_key = config.get("remote_api_key")
    return {"X-API-Key": api_key} if api_key else {}


def fetch_next_prompt(config: dict) -> dict | None:
    url = config["remote_ui_base_url"].rstrip("/") + config["fetch_prompt_endpoint"]
    resp = requests.get(url, headers=_auth_headers(config), timeout=30)
    if resp.status_code == 204:
        return None
    resp.raise_for_status()
    return resp.json()


def upload_result(config: dict, job_id: str, video_path: Path) -> None:
    endpoint = config["upload_endpoint"].replace("{job_id}", job_id)
    url = config["remote_ui_base_url"].rstrip("/") + endpoint
    with open(video_path, "rb") as f:
        resp = requests.post(
            url, files={"video": (video_path.name, f, "video/mp4")},
            headers=_auth_headers(config), timeout=300,
        )
    resp.raise_for_status()


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
    output_path = output_dir / f"{job['job_id']}.mp4"
    generator.generate(job["prompt"], output_path)
    print(f"Saved {output_path}")

    if upload:
        upload_result(config, job["job_id"], output_path)
        print("Uploaded result to remote UI")

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
    client.ensure_running()
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
        did_work = run_once(config, generator, output_dir, None)
        if not did_work:
            time.sleep(config["poll_interval_seconds"])


if __name__ == "__main__":
    main()
