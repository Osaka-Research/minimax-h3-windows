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
from workflow_convert import ObjectInfoSource, WorkflowFlattener, find_prompt_nodes, is_widget_type

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

TEMPLATE_URLS = {
    "t2v": "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_t2v.json",
    "i2v": "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_i2v.json",
    "r2v": "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_r2v.json",
}


def apply_config_overrides(source_workflow: dict, config: dict) -> None:
    """Patches the source (UI-format) workflow in place with resolution/duration
    overrides from config.json, before flattening. These are baked in once at
    setup time (unlike the prompt, which pipeline.py substitutes per job), so
    changing them requires re-running this script (install.ps1 does that
    automatically on every run)."""
    aspect_ratio = config.get("video_aspect_ratio")
    megapixels = config.get("video_megapixels")
    if aspect_ratio is not None or megapixels is not None:
        for node in source_workflow["nodes"]:
            if node["type"] != "ResolutionSelector":
                continue
            wv = node.get("widgets_values") or []
            while len(wv) < 2:
                wv.append(None)
            if aspect_ratio is not None:
                wv[0] = aspect_ratio
            if megapixels is not None:
                wv[1] = megapixels
            node["widgets_values"] = wv

    duration = config.get("video_duration_seconds")
    if duration is not None:
        subgraph_defs = {sg["id"]: sg for sg in source_workflow.get("definitions", {}).get("subgraphs", [])}
        for node in source_workflow["nodes"]:
            sg_def = subgraph_defs.get(node["type"])
            if sg_def is None:
                continue
            # Duration is a widget-backed boundary input exposed directly on
            # the subgraph instance (not a separate node), identified by its
            # "duration" label - not by name, which varies (e.g. "value_1").
            duration_name = next(
                (inp["name"] for inp in node.get("inputs", [])
                 if str(inp.get("label", "")).lower() == "duration"),
                None,
            )
            if duration_name is None:
                continue
            widget_names = [inp["name"] for inp in sg_def["inputs"] if is_widget_type(inp["type"])]
            if duration_name not in widget_names:
                continue
            idx = widget_names.index(duration_name)
            wv = node.get("widgets_values") or []
            while len(wv) <= idx:
                wv.append(None)
            wv[idx] = duration
            node["widgets_values"] = wv


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
    apply_config_overrides(source_workflow, config)
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
