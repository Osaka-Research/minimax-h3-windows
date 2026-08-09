"""
One-time (re-runnable) setup step: downloads the official MiniMax H3
text-to-video ComfyUI workflow template, flattens it into API format using
a live ComfyUI instance's own node schemas, and writes workflow_api.json +
patches config.json's prompt_node_id/prompt_input_key - fully automating
what would otherwise be a manual "export from the browser UI" step.

Run by install.ps1 after ComfyUI + model weights are set up. Safe to
re-run any time (e.g. after a ComfyUI/H3 node update) to regenerate.
"""

import json
import sys
from pathlib import Path

import requests

from comfy_client import ComfyUIClient
from workflow_convert import ObjectInfoSource, WorkflowFlattener, find_prompt_nodes

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

TEMPLATE_URLS = {
    "t2v": "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_t2v.json",
    "i2v": "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_i2v.json",
    "r2v": "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_r2v.json",
}


def main():
    if not CONFIG_PATH.exists():
        sys.exit("config.json not found - run install.ps1 first, or copy config.example.json.")
    config = json.loads(CONFIG_PATH.read_text())

    template_kind = config.get("workflow_template", "t2v")
    template_url = TEMPLATE_URLS[template_kind]

    print(f"Downloading official MiniMax H3 {template_kind} workflow template...")
    resp = requests.get(template_url, timeout=30)
    resp.raise_for_status()
    source_workflow = resp.json()
    (ROOT / "workflow_source.json").write_text(json.dumps(source_workflow, indent=2))

    client = ComfyUIClient(config, ROOT)
    client.ensure_running()

    print("Fetching node schemas from ComfyUI (/object_info)...")
    object_info = ObjectInfoSource(client.object_info())

    print("Flattening workflow to API format...")
    flattener = WorkflowFlattener(source_workflow, object_info)
    flat = flattener.flatten()

    candidates = find_prompt_nodes(flat)
    if not candidates:
        sys.exit(
            "Could not find a text-prompt input in the flattened workflow. "
            "The template may have changed shape - inspect workflow_api.json "
            "manually and set prompt_node_id/prompt_input_key in config.json."
        )
    if len(candidates) > 1:
        print(f"Warning: multiple prompt-shaped inputs found {candidates}, using the first.")
    node_id, input_key = candidates[0]

    workflow_path = ROOT / config["workflow_path"]
    workflow_path.write_text(json.dumps(flat, indent=2))

    config["prompt_node_id"] = node_id
    config["prompt_input_key"] = input_key
    CONFIG_PATH.write_text(json.dumps(config, indent=2))

    print(f"Wrote {workflow_path.name} ({len(flat)} nodes).")
    print(f"Prompt injection point: node {node_id!r}, input {input_key!r} - saved to config.json.")
    print("Workflow setup complete - no manual export needed.")


if __name__ == "__main__":
    main()
