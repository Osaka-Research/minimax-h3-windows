# MiniMax H3 (Hailuo 3.0) Video Generation Pipeline for Windows — Single GPU, No Manual Setup

Self-hosted, fully automated Windows pipeline for running **MiniMax H3**
(Hailuo 3.0) — MiniMax's open-weight text-to-video model — on a single
NVIDIA gaming GPU via ComfyUI. Submit a prompt from any device through the
companion remote UI, and the Windows machine generates the video locally
and uploads the result back. One command installs everything; it
auto-starts on boot/login and self-heals if anything crashes.

- **One-command install** — nothing needs to be preinstalled, not even
  Python or Git.
- **Runs on a single gaming GPU** (12GB+ NVIDIA), not the 4-GPU setup
  MiniMax's own docs describe, via ComfyUI's quantized checkpoints.
- **No manual ComfyUI workflow export** — the browser-only "Save (API
  Format)" step is reimplemented in Python.
- **Auto-starts on boot/login** and **self-heals**: crashed jobs retry,
  a crashed ComfyUI restarts, a crashed worker process comes back within
  15 minutes via a watchdog.
- **Pairs with [`minimax-h3-server`](https://github.com/Osaka-Research/minimax-h3-server)**
  — a small hosted remote UI to submit prompts and watch/download finished
  videos from anywhere, deployed separately from the Windows machine(s).

## Install

Paste this into a PowerShell prompt on the Windows machine with the GPU:

```powershell
irm https://raw.githubusercontent.com/Osaka-Research/minimax-h3-windows/main/bootstrap.ps1 | iex
```

That's the whole install: it sets the execution policy for this session,
fetches the repo, installs Python/Git/`winget` itself if any are missing,
installs CUDA-enabled PyTorch, ComfyUI, and the quantized MiniMax H3
checkpoints (~42.5GB), builds the ComfyUI workflow automatically, and
registers auto-start. See [Prerequisites](#prerequisites) for the one thing
it can't do for you, and [Configuration](#configuration) for the one value
you edit afterward.

As with any `irm | iex` one-liner, it's worth glancing at
[`bootstrap.ps1`](bootstrap.ps1) first since it runs unreviewed code on
your machine.

<details>
<summary>Installing manually instead (clone the repo yourself)</summary>

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; cd minimax-h3-pipeline; ./install.ps1
```

The execution-policy bypass is needed here specifically because this runs
`install.ps1` as a file — the one-liner above doesn't hit this since it
pipes into `iex` instead. Pass `-SkipAutostart` to `install.ps1` if you
don't want it registered to auto-start.
</details>

## Prerequisites

The one thing this project genuinely can't set up for you: an **NVIDIA GPU
with a current driver**. Everything else - PowerShell's execution policy,
Python, Git, `winget` itself if that's even missing, the CUDA build of
PyTorch specifically (not the CPU-only one plain `pip install torch` gives
you on Windows), the Visual C++ Redistributable ComfyUI needs - is checked
and auto-installed by `bootstrap.ps1`/`install.ps1`. Nothing needs to be
preinstalled to run the one-liner above, down to and including Python and
Git themselves (`bootstrap.ps1` downloads a zip snapshot instead of using
`git clone` if git isn't there yet; `install.ps1` then installs Python and
Git properly via `winget` - bootstrapping `winget` first via Microsoft's
official installer link if that's missing too). This has **not** been run
end-to-end on a real Windows/GPU machine, since none was available while
building it - see [Known issues](#known-issues-and-troubleshooting) below
for what to watch for on a first run, and what happens if something can't
auto-install (clear error message + manual install link, never a silent
failure).

Model: [`Comfy-Org/MiniMax-H3`](https://huggingface.co/Comfy-Org/MiniMax-H3)
(released 2026-08-03) — the ComfyUI-repackaged, quantized mirror of
[`MiniMaxAI/MiniMax-H3`](https://huggingface.co/MiniMaxAI/MiniMax-H3).

**Why ComfyUI and not "just load the model in Python":** the official
weights are BF16 (~118GB across diffusion model + text encoder) and MiniMax's
own docs recommend 4 GPUs for production. That doesn't fit one gaming card.
Comfy-Org publishes reduced checkpoints instead — pruned INT8 diffusion model
(~21GB) + NVFP4 text encoder (~15.7GB) + video/audio VAEs (~5.8GB), ~42.5GB
total — and ComfyUI itself handles VRAM/RAM offloading, which a plain
`diffusers.from_pretrained(...).to("cuda")` call does not. ComfyUI's own
docs claim a 12GB card plus offloading can run it; this hasn't been
independently verified here, so treat it as a starting point, not a
guarantee. Local generation runs at 768px short-edge (2K needs a second
upscale pass).

## How the ComfyUI workflow is generated automatically

MiniMax H3 ships as native ComfyUI support, driven through the browser UI
with an official template. Talking to it from a script requires the
*API-format* graph (flat `{node_id: {class_type, inputs}}`), but the
official template on GitHub
([`Comfy-Org/workflow_templates`](https://github.com/Comfy-Org/workflow_templates))
is in the browser's *UI* graph format instead — nodes/links/positions, and
for H3 specifically, the whole model wrapped in a **subgraph** (a
graph-within-a-node). Converting UI format → API format normally only
happens inside ComfyUI's frontend ("Save (API Format)"); there's no
server-side endpoint for it.

`setup_workflow.py` reimplements that conversion instead of requiring a
browser click:
1. Downloads the real official `video_minimax_h3_t2v.json` template
   (verified against the actual file, not guessed).
2. Asks a running ComfyUI for `/object_info` — the authoritative, live,
   version-matched schema for every node class (including H3's custom
   nodes), so widget vs. socket handling always matches your actual
   ComfyUI/H3 install rather than a hardcoded assumption.
3. Flattens the graph in `workflow_convert.py`, including inlining the H3
   subgraph — resolving each of its boundary inputs to either the outer
   node's connected link or its widget value, exactly as ComfyUI's own
   exporter would (verified by running it against the real downloaded
   template and confirming the outer prompt text correctly overrides the
   subgraph's internal placeholder prompt).
4. Scans the flattened graph for the node/field literally named `prompt`
   and writes `prompt_node_id` / `prompt_input_key` into `config.json`
   automatically.

Output: `workflow_api.json` (what `pipeline.py` submits, with the prompt
substituted in) and `workflow_source.json` (the original, for reference).
`install.ps1` runs this for you; re-run `python setup_workflow.py` by hand
any time (e.g. after a ComfyUI or H3 update) to regenerate both.

## Configuration

`remote_ui_base_url` in `config.example.json` already points at the
deployed [`minimax-h3-server`](https://github.com/Osaka-Research/minimax-h3-server)
instance, and there's nothing else to edit by hand — the server has no
auth, so no key to set (see that repo's README for the tradeoff this makes):

```jsonc
{
  "remote_ui_base_url": "https://minimax-h3-server.onrender.com",
  "worker_id": null,                           // defaults to this machine's hostname if unset
  "fetch_prompt_endpoint": "/jobs/next",       // GET -> {"job_id": "...", "prompt": "..."} or 204 if none queued
  "upload_endpoint": "/jobs/{job_id}/result",  // POST multipart file upload
  "fail_endpoint": "/jobs/{job_id}/fail",      // POST {"error": "..."}
  "poll_interval_seconds": 10,
  "output_dir": "outputs",

  "comfyui_dir": "ComfyUI",
  "comfyui_host": "127.0.0.1",
  "comfyui_port": 8188,
  "comfyui_extra_args": ["--lowvram"],

  "workflow_template": "t2v",
  "workflow_path": "workflow_api.json",
  "prompt_node_id": "126",        // <- auto-filled by setup_workflow.py
  "prompt_input_key": "prompt"    // <- auto-filled by setup_workflow.py
}
```

`fetch_prompt_endpoint`/`upload_endpoint`/`fail_endpoint` already match
`minimax-h3-server`'s routes by default.

## Run

```powershell
./run.bat
```

or manually:

```powershell
.venv\Scripts\activate
python pipeline.py            # poll loop against config.json
python pipeline.py --once     # single poll-generate-upload cycle, then exit
python pipeline.py --prompt "a corgi surfing at sunset"  # local test, skips remote fetch, generates + saves only (no upload)
```

`pipeline.py` starts ComfyUI itself as a subprocess if it isn't already
running (using `comfyui_extra_args`, e.g. `--lowvram`).

## Auto-start on Windows boot/login

`install.ps1` sets this up by default (skip with `-SkipAutostart`, or re-run
`install.ps1` any time to re-register it) — it both **registers** the task
for future logons *and* **starts it immediately**. It creates a Scheduled
Task (`MiniMaxH3Pipeline`) that:
- Triggers **at user logon** (not raw system boot — GPU drivers and your
  user profile/venv need the session to exist first) **and** every 15
  minutes as a watchdog (a no-op if already running, via
  `MultipleInstances: IgnoreNew`) - so it comes back on its own if it ever
  goes down, not just at the next logon. See
  [Failure handling](#failure-handling-and-self-healing) below.
- Runs hidden (no console window), via `run-background.bat`.
- Auto-restarts up to 5 times, 1 minute apart, on top of the watchdog above.
- Has no execution time limit (it's meant to run indefinitely).
- Does **not** require admin rights, since it runs in your own session
  rather than as SYSTEM.

Check on it:

```powershell
Get-ScheduledTask -TaskName MiniMaxH3Pipeline | Get-ScheduledTaskInfo   # last run time/result
Start-ScheduledTask -TaskName MiniMaxH3Pipeline                        # start now, without rebooting
Get-Content .\logs\pipeline.log -Tail 50 -Wait                         # tail live output
```

Remove autostart: `./uninstall-autostart.ps1`

Note: if you want it running even when nobody is logged in (true headless
service), that needs `-RunLevel Highest` + stored credentials in
`Register-ScheduledTask` and admin rights to set up — ask if you want that
variant instead.

## Running on multiple devices

Install this on more than one Windows machine and point them all at the
same `minimax-h3-server` instance — there's no per-device setup beyond
that. How work gets distributed falls out of the design rather than being a
separate feature:

- **No device registration or routing.** Every machine polls the same
  `GET /jobs/next`. Whichever one's request lands first claims the oldest
  queued job - first-come-first-served, not round-robin or capability-aware.
- **A busy device doesn't compete for new work.** `pipeline.py` only polls
  again after it finishes its current job (generate + upload happen
  synchronously in the loop), so an idle second device is what picks up the
  next job. With only one device and it's busy, a new job just sits queued
  until that device finishes and polls again (every `poll_interval_seconds`).
- **Failover isn't sticky to one device.** If a device fails a job or goes
  silent (crash, network loss), the job goes back into the shared queue via
  the retry/stale-reclaim logic in [Failure handling](#failure-handling-and-self-healing)
  below - *any* device can pick it up next, not just the one that failed.
  `MAX_JOB_RETRIES` counts attempts across all devices combined, so a
  fundamentally broken prompt won't bounce forever between every machine
  you own.
- **The claim itself is race-safe under real concurrency**, not just in
  theory: verified by running the server under `gunicorn -w 4` and firing
  20 genuinely concurrent claim requests at 5 queued jobs — each job was
  claimed by exactly one request, the other 15 correctly got nothing.
  (`next_job()` re-checks the job's status inside the claiming `UPDATE`'s
  `WHERE` clause and retries on the rare row another request claims first,
  rather than a plain select-then-update that a fast enough race could
  double-claim.)
- **The `/` page shows which device claimed each job** (`X-Worker-Id`
  header, defaults to the machine's hostname; override via `worker_id` in
  `config.json`) alongside its attempt count - useful for seeing your
  workload spread across machines, or spotting one that's failing a lot.

## Failure handling and self-healing

This is meant to run unattended, so failures at any layer are handled
automatically rather than requiring someone to notice and restart things:

- **A single bad job** (bad prompt, ComfyUI OOM, transient network error)
  doesn't crash the worker. `pipeline.py` catches per-job errors, reports
  them to the server (`POST /jobs/<id>/fail`), and moves on to the next
  poll. The server re-queues that job for another automatic attempt (up to
  `MAX_JOB_RETRIES`, default 3) before marking it permanently `failed`.
- **ComfyUI dying mid-run** is checked and restarted before every job
  (`ensure_running()`), not just once at process startup.
- **The worker process itself crashing** (including before it can report
  any failure - power loss, OOM-killed, etc.) is handled two ways: retries
  with backoff around the whole poll loop and initial ComfyUI startup
  inside `pipeline.py` first; failing that, the job stays claimed
  ("in_progress") on the server but is automatically treated as available
  again after `STALE_CLAIM_TIMEOUT_MINUTES` (default 30) - so it still gets
  retried even if the client never says a word. Jobs that exhaust
  `MAX_JOB_RETRIES` this way also resolve to `failed` (with an explanatory
  error message) instead of sitting stuck forever.
- **The Windows process disappearing entirely** (crash outside Python's
  control) is covered by the Scheduled Task: on top of the 5-attempts/
  1-minute restart-on-failure, `install.ps1` also adds a 15-minute
  recurring trigger (`MultipleInstances: IgnoreNew`, so it's a no-op if
  already running) as a watchdog - so it comes back within 15 minutes no
  matter how many times it's already failed, rather than giving up until
  the next logon.

Tune the server's retry/timeout knobs via env vars:
`MAX_JOB_RETRIES` (default 3), `STALE_CLAIM_TIMEOUT_MINUTES` (default 30).
Failed jobs and their error messages/attempt counts show up on the `/` page.

## Project structure

- `bootstrap.ps1` — the one-command installer entry point (`irm ... | iex`,
  see Install above); fetches the repo (via git, or a zip if git isn't
  installed yet) and runs `install.ps1`.
- `install.ps1` — ensures Python and Git are present (installing them via
  `winget` if not - bootstrapping `winget` itself first if needed), clones
  ComfyUI, creates a venv, installs deps (CUDA PyTorch explicitly, then the
  rest), downloads the quantized checkpoints into `ComfyUI/models/...`,
  runs `setup_workflow.py`, and (by default) registers + starts auto-start
  at logon. Pass `-SkipAutostart` to opt out. Re-runnable; every step is
  skipped/replaced idempotently.
- `setup_workflow.py` — the automated UI→API workflow conversion described
  above.
- `workflow_convert.py` — the general graph-flattening algorithm (subgraph
  inlining, widget/socket resolution) it uses.
- `comfy_client.py` — shared ComfyUI process launcher + HTTP API client,
  used by both `setup_workflow.py` and `pipeline.py`.
- `uninstall-autostart.ps1` — removes the scheduled task only.
- `requirements.txt` — deps for the orchestration scripts themselves
  (`ComfyUI/requirements.txt` covers ComfyUI's own, much larger, dependency set).
- `config.example.json` — copy to `config.json`; remote UI endpoints and
  ComfyUI launch settings. `prompt_node_id`/`prompt_input_key` start `null`
  and get filled in automatically by `setup_workflow.py`.
- `workflow_api.json` / `workflow_source.json` — generated by
  `setup_workflow.py`, not hand-written.
- `pipeline.py` — the runtime: starts ComfyUI if it isn't already running,
  then loops: fetch prompt from remote UI → submit workflow to ComfyUI's
  HTTP API → poll for completion → download result → upload back.
- `run.bat` / `run-background.bat` — interactive vs. logged-to-file launchers.

The "remote UI" itself (prompt-submission page + job queue + video storage
implementing the other end of this contract) lives in a separate repo,
[`minimax-h3-server`](https://github.com/Osaka-Research/minimax-h3-server)
— it's deployed independently of the Windows machine(s) (a VPS, a hosting
platform, a tunnel) and is what `remote_ui_base_url` should point at.

## Known issues and troubleshooting

- **None of `install.ps1`/`bootstrap.ps1` have been run on a real Windows
  machine** — there was no Windows/GPU box available while building this.
  What's here reflects checking real documentation for each gap found
  (e.g. ComfyUI's own Windows install docs for the CUDA-torch ordering,
  confirmed default-execution-policy and CPU-only-torch behavior via
  search) rather than guessing, but "should work based on docs" isn't the
  same as "verified working." If `install.ps1` fails partway, the error
  should point at what broke — re-running it is safe (every step is
  idempotent) after fixing that one thing.
- **Visual C++ Redistributable install via `winget`** is attempted silently
  (output suppressed) since it should be a no-op if already present; if
  ComfyUI fails to start with a DLL-related error, install it manually from
  the link `install.ps1` prints.
- **12GB VRAM claim is ComfyUI's own, unverified here.** If generation OOMs
  on your card, try switching `diffusion_models` to the smaller
  `pruned_fp8_scaled` checkpoint (fallback, per Comfy-Org's own notes;
  you'd need to update it in ComfyUI's model folder and re-point the
  workflow) or add `--novram`/`--cpu` to `comfyui_extra_args` at the cost
  of speed.
- **The flattener (`workflow_convert.py`) was built and verified against
  the actual downloaded T2V template** (confirmed it correctly resolves
  the subgraph boundary — e.g. the real prompt text correctly overrides the
  template's internal placeholder prompt — and correctly locates the single
  `prompt` input). It has not been run end-to-end against a live ComfyUI
  server or on real GPU hardware, since that requires a Windows/GPU
  machine this was never tested against. If ComfyUI or the H3 node
  definitions change shape, `setup_workflow.py` will either fail loudly
  (missing `/object_info` schema, no `prompt` field found) or you'll see
  it in the first `--prompt` test run — it's not designed to fail silently
  with wrong output.
- **Output key in `wait_for_result`** (`comfy_client.py`) checks
  `videos`/`gifs`/`images` in that order in ComfyUI's `/history` response,
  since H3's exact output key wasn't confirmed against a live run — verify
  against your first real generation and adjust if needed.

Before trusting this unattended: run `python pipeline.py --prompt "test"`
once by hand and confirm `outputs/local-test.mp4` is a real, correct video.
