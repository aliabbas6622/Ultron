# Serving a custom architecture: read the package, then set flags

Before you write a `max serve` command, read two files: the package's `arch.py`
(the `SupportedArchitecture`) and the checkpoint's `config.json`. Between them
they determine most of the flags: the encoding, the device, the max length, and
whether you need trust-remote-code or a chat template.

## Find the package

The package is the directory whose `__init__.py` exports a top-level
`ARCHITECTURES` list. If you don't already have the path, grep for it:

```bash
grep -rl 'ARCHITECTURES' <repo-or-dir>
```

Open that directory's `arch.py` (the `SupportedArchitecture(...)` call) and the
checkpoint's `config.json`; those two files drive the flags below. MAX imports
the package, registers every entry in `ARCHITECTURES` by `name`, and matches a
request's `config.json::architectures[0]` against a registered `name`. A package
can register several archs (for example a vision variant and a text-only causal
variant); MAX picks the one whose `name` matches the checkpoint.

## Read `arch.py` → derive flags

The `SupportedArchitecture(...)` call is a flag spec. Map its fields:

| `arch.py` field | Drives | How |
|---|---|---|
| `name=` | which checkpoint it serves | must equal `config.json::architectures[0]` **exactly** |
| `default_encoding=` | `--quantization-encoding` | this is the Hub-shipped dtype; use it unless you have a reason not to |
| `supported_encodings={...}` | valid `--quantization-encoding` values | whatever you pass must be in this set, or serve rejects it |
| `multi_gpu_supported=` | whether `--devices gpu:0,1,...` is allowed | `False` → single GPU only |
| `task=` | `--task` | only pass `--task` if the same `name` is registered for multiple tasks |
| `weight_adapters={...}` | which weight formats load | needs an entry for the checkpoint's format (`safetensors`/`gguf`) |
| `requires_max_batch_context_length=True` | VL models | signals you'll likely need `--max-length` set explicitly |

A `chat_template.jinja` sitting in the package directory means the model needs
`--chat-template path/to/chat_template.jinja` for chat requests to format
correctly.

## Read `config.json` → sanity-check and cap

- `architectures[0]`: must match `arch.py`'s `name`. A mismatch is the #1
  custom-arch serve failure.
- `torch_dtype` / `quantization_config`: the checkpoint's real precision.
  Confirm `default_encoding` agrees (a fp8 checkpoint with a `bfloat16` default
  is a red flag).
- `max_position_embeddings` (or `n_positions`): the **ceiling** for
  `--max-length`. Passing a larger value makes `max serve` abort. Tiny test
  checkpoints often declare 512; always cap at `min(what_you_want, this)`.
- MoE signals (`num_experts`, `num_local_experts`, `n_routed_experts`): a
  routed-MoE model benefits from `--device-graph-capture` and usually wants a
  larger `--max-length`.

## Encoding and device

MAX serves on GPU by default, so for the common case you don't set `--devices`
at all. The encoding just constrains what's possible:

| Encoding | Runs on |
|---|---|
| `float32` | CPU (pass `--devices cpu`) or GPU |
| `bfloat16`, `float16` | GPU (the default) |
| `float8_e4m3fn`, `float8_e5m2`, `float4_e2m1fnx2`, `gptq` | GPU only |

MAX doesn't pick the device from the encoding; it uses the GPU default either
way, which is what the GPU-only encodings need. Reach for `--devices` only to
force CPU (a `float32` checkpoint with the RAM to hold it), pin specific GPUs, or
shard across them.

## The flags that aren't obvious from the model

These come from *how MAX serves*, not from the checkpoint. They bite custom-arch
bring-ups repeatedly:

- **`--no-enable-overlap-scheduler --force`**: MAX auto-enables the overlap
  scheduler for single-step decode, and that path is incompatible with
  `logprobs` requests (which parity/verification tools send). If you're going to
  request logprobs, disable it; the `--force` is required to bypass the
  auto-enable heuristic. For plain chat serving you can leave it on.
- **`--trust-remote-code`**: needed when the checkpoint ships custom
  `modeling_*.py` / `configuration_*.py` (for example `internlm2`, `openelm`). MAX's
  loader also needs the repo's `auto_map` in `config.json` *and* those `.py`
  files present in the model directory. Without the flag you get "Transformers
  does not recognize this architecture" at load.
- **Don't serve on `--port 8001`.** The metrics ASGI app defaults to 8001 and
  binds *before* the main API server. If the main server also gets 8001, the log
  prints `Server ready` and then immediately `Shutting down workers…` with no
  error, and every request fails with a confusing JSON decode error. Use 8000 or
  another port; if you run multiple servers, also move the metrics port with the
  `MAX_SERVE_METRICS_ENDPOINT_PORT` env var.

## A worked custom-arch command

For a dense bf16 decoder on one GPU, pointing `--custom-architectures` at the
package directory (absolute path is safest for scripts and remote hosts):

```bash
max serve \
  --model-path <hf-repo-or-local-ckpt> \
  --custom-architectures /abs/path/to/my_arch \
  --devices gpu:0 \
  --quantization-encoding bfloat16 \
  --max-length 4096 \
  --trust-remote-code \
  --served-model-name my_arch \
  --port 8000
```

For a routed-MoE model, add `--device-graph-capture` and a larger
`--max-length`; for a model with a bundled template, add
`--chat-template /abs/path/to/my_arch/chat_template.jinja`. Both the bare
directory path and the `IMPORT_PATH:MODULE_NAME` colon form work for
`--custom-architectures`; the directory path is what most tooling uses.

## Readiness on a slow (MoE / large) compile

Custom archs often compile longer than built-ins. Watch the log for
`Still compiling model (Ns elapsed)`. An advancing counter is healthy; only a
*frozen* counter (or a log whose mtime stops moving while the process is alive)
is a real stall. Give large models a generous timeout (10+ minutes) before
treating a compile as hung.
