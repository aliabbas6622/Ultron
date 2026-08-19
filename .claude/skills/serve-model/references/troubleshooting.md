# `max serve` startup failures

Read the serve log first. The real error is usually a few lines **above** the
final traceback; the traceback is often just the outer frame. Match the
symptom below.

| Symptom (in the log or on request) | Cause | Fix |
|---|---|---|
| `... not found in registry` / no architecture matches | The checkpoint's `architectures[0]` doesn't match any registered `SupportedArchitecture.name` | For custom arch: make `name=` in `arch.py` equal `config.json::architectures[0]` **exactly**. For built-in: the arch isn't supported, so you need a custom arch package (a bring-up task). |
| `Refusing to override existing architecture for '<Name>'` | Your custom arch's `name=` matches a **built-in** arch's name. The docs say custom takes precedence, but some MAX versions have a registration-ordering bug that refuses the override. | Give your `SupportedArchitecture` a distinct `name` (for example `LlamaCustomForCausalLM`) and set the checkpoint's `config.json::architectures[0]` to that same name (in a local copy of the config; symlink the weights, don't re-download). Then the custom arch is the only match. If you can't edit the package, this is a blocker; report it. |
| `TypeError: <Model>.__init__() got an unexpected keyword argument 'max_batch_size'` (or another kwarg) | **Package drift**: the package was written for an older MAX and its `PipelineModel.__init__` signature is behind the current one. MAX now passes an argument the model doesn't accept. No serve flag avoids this (it's on every construction path). | One-line source fix: make the model's `__init__` forward through, `def __init__(self, pipeline_config, session, *args, **kwargs): super().__init__(pipeline_config, session, *args, **kwargs)`, on **every** `PipelineModel` subclass in the package (a subclass that itself subclasses another local model needs the fix on both). If you can't edit the package, pin to the MAX version it was verified against. |
| `AssertionError: Expected max_batch_size to be set` | Some custom models require an explicit batch size; MAX didn't set a default. | Pass `--max-batch-size 1` (raise for throughput). The bundled inspector adds this by default. |
| `compatible weights cannot be found` / encoding error | The `--quantization-encoding` (or auto-detected one) isn't in the arch's `supported_encodings`, or doesn't match what the checkpoint ships | Set `--quantization-encoding` to the checkpoint's real format (check `config.json::torch_dtype` / `quantization_config`); confirm it's listed in `supported_encodings`. |
| `trust_remote_code` error at load | The HF repo ships custom `modeling_*.py` / tokenizer the loader must execute | Add `--trust-remote-code` (only for repos you trust). |
| Missing / no weight adapter for format | `weight_adapters` has no entry for the checkpoint's weight format | Add a `WeightsFormat.safetensors` (or `.gguf`) entry to the `SupportedArchitecture`. |
| CUDA OOM / allocation failed at load | Weights + KV cache exceed device memory | Lower `--max-length`, lower `--max-batch-size`, raise or lower `--device-memory-utilization`, use fewer/more GPUs, or a smaller/quantized checkpoint. |
| `ModuleNotFoundError` importing the arch package | `--custom-architectures` path/module resolution is wrong | Use the colon form `IMPORT_PATH:MODULE_NAME` where IMPORT_PATH is the *parent* dir on `sys.path` and MODULE_NAME is the package (dir with `__init__.py`). |
| `Address already in use` / port bind fails | Another server holds the port | Pick a free `--port`, or kill the stale `max serve` process. |
| Wrong GPU / device routing under multi-process | `--devices` and `CUDA_VISIBLE_DEVICES` both set | Use only `--devices`; unset `CUDA_VISIBLE_DEVICES`. |
| Server "hangs" with no output | Large model still compiling | Look for `Still compiling model (Ns elapsed)` heartbeats; if the counter advances, wait. Only a *frozen* counter is a real stall. |
| Server green but output is repetition / gibberish | Serving works; model correctness (parity) is wrong | Not a serve problem. This is a bring-up/parity issue; the graph or weights don't match the reference. |
| `logprobs` request rejected with 400 | Runtime config can't honor logprobs | Add `--allow-unsupported-logprobs` to downgrade to a warning, or drop the logprobs request. |

## Readiness and failure markers to grep in the log

- Ready: `Server ready`, `Uvicorn running`, `Application startup complete`
- Compiling (be patient): `Still compiling model (Ns elapsed)`
- Failed: `Traceback`, `CUDA_ERROR`, `compatible weights cannot be found`,
  `not found in registry`

## Health checks

```bash
curl -s http://localhost:8000/v1/health    # 200 when ready
curl -s http://localhost:8000/v1/models     # lists the served model name
```
