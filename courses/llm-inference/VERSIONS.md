# Version qualification

The introductory Lab 35 was executed locally on CPU with an existing PyTorch
2.13.0 environment. This is algorithmic smoke evidence only: it does not
qualify the separate mechanics/serving environments, authorize a dependency
fallback or establish CUDA/H100 behavior. It downloads no model or engine.

| Component | Course target | Evidence status |
| --- | --- | --- |
| Python | 3.12 | Local serving environment exists; cluster parity pending |
| PyTorch | 2.14.0 mechanics-manifest authority | Clean Linux/H100 install and qualification pending; no fallback approved |
| Transformers | Pinned mechanics candidate set | Target installation and joint compatibility pending |
| vLLM | 0.28 profile | Linux/H100 install and engine activation pending |
| TensorRT-LLM and Triton | Exact support-matrix container digest | Not selected until target qualification |
| AIPerf | Exact client version matched to server profile | Target qualification pending |
| Dynamo | Advanced exact container profile | Deferred pending transport/topology qualification |

Do not substitute an unqualified “latest” image. Once validated, record the
full immutable digest, model revision, driver, runtime, and launch arguments.

## KV-retention policy qualification

Lab 36 uses only Python standard-library code and course result helpers. Its
capacities, transfer rates and prefill costs are declared modeling inputs, not
measurements. Running the model does not qualify CUDA, a serving engine,
persistent KV recovery or a GPUDirect Storage path.
