# Official Source Map

Use these official documentation entry points when researching concrete AI
technologies. This map is a starting point, not proof that a feature, release,
model, region, extension, or integration is suitable for the target workload.
Verify the exact version and behavior before selecting it.

Baseline date: 2026-08-14

Prefer evidence in this order:

1. Current official specification or product documentation.
2. Current release notes, lifecycle policy, and compatibility matrix.
3. Official source repository and tagged implementation.
4. Reproducible conformance test or target-environment measurement.

Do not use a third-party comparison page as the authority for protocol,
security, lifecycle, data-handling, or compatibility behavior.

## Contents

- AAIF and open agentic standards
- Agent application frameworks and harnesses
- Model providers, SDKs, and gateways
- MCP core, extensions, SDKs, and frameworks
- PyTorch and distributed training
- Hugging Face model and training tooling
- Large-model training frameworks
- Hardware and Nebius AI Cloud
- Inference engines and serving
- Retrieval, embeddings, and AI data
- Lifecycle, evaluation, and observability
- Safety and security
- Research record

## AAIF And Open Agentic Standards

### Agentic AI Foundation

- AAIF home and projects: <https://aaif.io/projects/>
- AAIF formation and founding projects: <https://aaif.io/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation-aaif-anchored-by-new-project-contributions-including-model-context-protocol-mcp-goose-and-agents-md/>
- MCP project: <https://aaif.io/projects/model-context-protocol/>
- goose project: <https://aaif.io/projects/goose/>
- AGENTS.md project: <https://aaif.io/projects/agents-md/>
- agentgateway project: <https://aaif.io/projects/agentgateway/>
- agentgateway donation and architecture: <https://aaif.io/blog/agentgateway-joins-aaif-as-an-open-gateway-for-agentic-ai-infrastructure/>

Treat AAIF as neutral governance and an ecosystem of composable projects. Do
not treat it as a complete application framework or as proof that every hosted
project release supports the latest version of every protocol.

### Repository Instructions And Skills

- AGENTS.md: <https://agents.md/>
- Agent Skills: <https://agentskills.io/>
- Agent Skills specification and source: <https://github.com/agentskills/agentskills>
- Claude Agent Skills: <https://code.claude.com/docs/en/agent-sdk/skills>

Use AGENTS.md for repository-local guidance. Use Agent Skills for reusable,
progressively loaded procedures, scripts, references, and assets. Apply normal
software supply-chain controls to Skills that contain executable content.

### Agent-To-Agent And User Interaction

- A2A v1.0 home: <https://a2a-protocol.org/latest/>
- A2A v1.0 announcement: <https://a2a-protocol.org/latest/announcing-1.0/>
- A2A current specification: <https://a2a-protocol.org/latest/specification/>
- A2A governance: <https://a2a-protocol.org/latest/governance/>
- A2A compatibility kit: <https://github.com/a2aproject/a2a-tck>
- Agent Client Protocol: <https://zed.dev/acp>
- ACP specification repository: <https://github.com/agentclientprotocol/agent-client-protocol>
- AG-UI protocol: <https://docs.ag-ui.com/>
- CopilotKit AG-UI documentation: <https://docs.copilotkit.ai/deepagents/agentic-protocols/ag-ui>

Use MCP for agent or host access to tools and context. Use A2A v1.0 for
communication between independently deployed, opaque agent services, and pin
`A2A-Version` plus required Agent Card capabilities. Use ACP for coding agent
to editor integration. Use AG-UI for runtime-to-frontend event interoperability.

### AAIF Runtime And Gateway Projects

- goose documentation: <https://block.github.io/goose/>
- agentgateway documentation: <https://agentgateway.dev/docs/>
- agentgateway MCP integration: <https://agentgateway.dev/docs/local/main/integrations/mcp-servers/>
- agentgateway LLM integration: <https://agentgateway.dev/docs/local/main/integrations/llm-providers/>
- Kubernetes Gateway API: <https://gateway-api.sigs.k8s.io/>

Verify exact MCP, A2A, provider, policy, and telemetry behavior for the selected
agentgateway release. Neutral governance is not a conformance result.

## Agent Application Frameworks And Harnesses

### Recommended Python Kernel Candidates

- Pydantic AI: <https://ai.pydantic.dev/>
- Pydantic AI model overview: <https://ai.pydantic.dev/models/overview/>
- Pydantic AI providers: <https://ai.pydantic.dev/models/providers/>
- Pydantic AI model profiles: <https://ai.pydantic.dev/models/profiles/>
- Pydantic AI Anthropic models: <https://ai.pydantic.dev/models/anthropic/>
- Pydantic AI Google and Gemini models: <https://ai.pydantic.dev/models/google/>

Verify model profiles, provider adapters, feature flags, error behavior, and
per-user MCP identity. Do not assume OpenAI-compatible providers implement the
same semantics as the OpenAI API.

### LangChain And LangGraph

- LangChain agents: <https://docs.langchain.com/oss/python/langchain/agents>
- LangChain tools: <https://docs.langchain.com/oss/python/langchain/tools>
- LangChain retrieval: <https://docs.langchain.com/oss/python/langchain/retrieval>
- LangGraph overview: <https://docs.langchain.com/oss/python/langgraph/overview>
- LangGraph persistence: <https://docs.langchain.com/oss/python/langgraph/persistence>
- LangGraph interrupts: <https://docs.langchain.com/oss/python/langgraph/interrupts>
- LangGraph time travel: <https://docs.langchain.com/oss/python/langgraph/use-time-travel>

Use LangChain components where they reduce integration work. Use LangGraph when
explicit graph state, checkpoints, interrupts, replay, or fault recovery are
requirements, not merely because an application contains more than one step.

### Provider-Native Agent SDKs

- OpenAI Agents SDK: <https://openai.github.io/openai-agents-python/>
- OpenAI Agents SDK models: <https://openai.github.io/openai-agents-python/models/>
- OpenAI Agents SDK MCP: <https://openai.github.io/openai-agents-python/mcp/>
- OpenAI Agents SDK human approval: <https://openai.github.io/openai-agents-python/human_in_the_loop/>
- OpenAI Agents SDK tracing: <https://openai.github.io/openai-agents-python/tracing/>
- Claude Agent SDK: <https://code.claude.com/docs/en/agent-sdk/overview>
- Claude Agent SDK MCP: <https://code.claude.com/docs/en/agent-sdk/mcp>
- Claude Agent SDK deployment security: <https://code.claude.com/docs/en/agent-sdk/secure-deployment>
- Claude Managed Agents: <https://platform.claude.com/docs/en/agents-and-tools/managed-agents/overview>

Treat provider-native SDKs as strong choices when their native agent behavior is
part of the product requirement. Put them behind application boundaries when
provider portability remains a requirement. Review tracing export defaults and
data policy before production use.

### Codex Engineering Specialists

- Codex SDK: <https://developers.openai.com/codex/sdk/>
- Codex app-server: <https://developers.openai.com/codex/app-server/>

Use the Codex SDK for coding-focused threads, CI, programmatic jobs, and
application integration. Add app-server as a separately owned protocol surface
only for deep clients that need Codex authentication, conversation history,
approvals, or streamed agent events. The Python Codex SDK already controls a
local app-server, so SDK use does not imply a second direct app-server client.

### Other Framework Families

- Microsoft Agent Framework: <https://learn.microsoft.com/en-us/agent-framework/>
- Microsoft Agent Framework 1.0 announcement: <https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/>
- Microsoft Agent Framework language and feature status: <https://learn.microsoft.com/en-us/agent-framework/get-started/>
- Microsoft Agent Framework workflows: <https://learn.microsoft.com/en-us/agent-framework/workflows/>
- Google Agent Development Kit: <https://google.github.io/adk-docs/>
- AWS Strands Agents: <https://strandsagents.com/latest/>
- LlamaIndex agents and workflows: <https://developers.llamaindex.ai/python/framework/understanding/agent/>
- Hugging Face smolagents: <https://huggingface.co/docs/smolagents/>
- CrewAI: <https://docs.crewai.com/>
- Temporal workflows: <https://docs.temporal.io/workflows>
- Temporal activities: <https://docs.temporal.io/activities>
- Temporal retries and failure detection: <https://docs.temporal.io/encyclopedia/detecting-activity-failures>

At the baseline date, Microsoft Agent Framework 1.0 is generally available for
.NET and Python; Go and selected features or extensions remain preview. Verify
the exact language, package, feature, provider, and hosting surface before
adoption, and review current migration guidance from older Microsoft agent
frameworks.

## Model Providers, SDKs, And Gateways

### OpenAI

- OpenAI API overview: <https://developers.openai.com/api/>
- OpenAI model catalog: <https://developers.openai.com/api/docs/models>
- OpenAI Responses API migration and capability guide: <https://developers.openai.com/api/docs/guides/migrate-to-responses>
- OpenAI direct text generation: <https://developers.openai.com/api/docs/guides/text>
- OpenAI embeddings: <https://developers.openai.com/api/docs/guides/embeddings>
- OpenAI audio and speech: <https://developers.openai.com/api/docs/guides/audio>
- OpenAI Realtime API: <https://developers.openai.com/api/docs/guides/realtime>
- OpenAI function calling: <https://developers.openai.com/api/docs/guides/function-calling>
- OpenAI structured outputs: <https://developers.openai.com/api/docs/guides/structured-outputs>
- OpenAI SDKs: <https://developers.openai.com/api/docs/libraries>
- OpenAI data controls: <https://developers.openai.com/api/docs/guides/your-data>

### Anthropic

- Claude API overview: <https://platform.claude.com/docs/en/api/overview>
- Claude model overview: <https://platform.claude.com/docs/en/about-claude/models/overview>
- Claude tool use: <https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview>
- Claude structured output: <https://platform.claude.com/docs/en/build-with-claude/structured-outputs>
- Anthropic SDKs: <https://platform.claude.com/docs/en/api/client-sdks>

### Nebius Token Factory

- Token Factory inference quickstart: <https://docs.nebius.com/studio/inference/quickstart>
- Function calling and tools: <https://docs.nebius.com/studio/inference/function-calling>

Token Factory provides an OpenAI-compatible surface, but model-level support for
tools, structured output, modalities, context, and streaming must be resolved
from the exact current model and endpoint documentation. Do not reduce the
provider contract to a base URL and API key or infer undocumented features from
the compatible wire shape.

### Gateways And Routing

- agentgateway: <https://agentgateway.dev/docs/>
- LiteLLM: <https://docs.litellm.ai/>
- Portkey AI Gateway: <https://portkey.ai/docs/>
- OpenRouter API: <https://openrouter.ai/docs/>

A gateway adds a compatibility and policy layer. Validate feature preservation,
error normalization, streaming, tool-call deltas, structured output, usage,
tracing, retention, and routing behavior against each upstream provider.

## MCP Core, Extensions, SDKs, And Frameworks

### Protocol Authority

- MCP latest specification: <https://modelcontextprotocol.io/specification/latest>
- MCP 2026-07-28 release: <https://blog.modelcontextprotocol.io/posts/2026-07-28/>
- MCP versioning: <https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning>
- MCP transports: <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports>
- MCP authorization: <https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization>
- MCP tools: <https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- MCP resources: <https://modelcontextprotocol.io/specification/2026-07-28/server/resources>
- MCP prompts: <https://modelcontextprotocol.io/specification/2026-07-28/server/prompts>
- MCP lifecycle and deprecations: <https://modelcontextprotocol.io/specification/2026-07-28/basic/lifecycle>

MCP core revisions use dates. Do not call the protocol "MCP v2." Record core,
extension, SDK, framework, gateway, and host versions separately.

### Official Extensions

- MCP Apps overview and SDK: <https://apps.extensions.modelcontextprotocol.io/>
- MCP Apps specification: <https://apps.extensions.modelcontextprotocol.io/specification/>
- MCP Apps SEP: <https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp>
- MCP Tasks overview: <https://tasks.extensions.modelcontextprotocol.io/>
- MCP Tasks specification: <https://tasks.extensions.modelcontextprotocol.io/specification/draft/tasks>
- MCP Tasks SEP: <https://modelcontextprotocol.io/seps/2663-tasks-extension>

Extensions version independently and require capability negotiation. MCP Apps
revision `2026-01-26` is Stable and uses identifier
`io.modelcontextprotocol/ui`; exact host support still requires verification. At
the baseline date, the Tasks site is marked Draft even though Tasks participates
in the official extension framework, so treat production adoption as
Conditional until the selected extension revision and host behavior are pinned
and tested.

### Official SDKs And Tiers

- MCP SDK overview: <https://modelcontextprotocol.io/docs/sdk>
- MCP SDK tiers: <https://modelcontextprotocol.io/community/sdk-tiers>
- TypeScript SDK: <https://github.com/modelcontextprotocol/typescript-sdk>
- Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- Go SDK: <https://github.com/modelcontextprotocol/go-sdk>
- C# SDK: <https://github.com/modelcontextprotocol/csharp-sdk>
- MCP conformance: <https://github.com/modelcontextprotocol/conformance>

Use a Tier 1 SDK as the protocol conformance authority. Verify the exact package
release, implemented SEPs, downgrade behavior, and extension support.

### FastMCP And Application UI Tooling

- FastMCP: <https://gofastmcp.com/>
- FastMCP Client: <https://gofastmcp.com/clients/client>
- FastMCP authentication: <https://gofastmcp.com/servers/auth/authentication>
- FastMCP OpenTelemetry: <https://gofastmcp.com/servers/telemetry>
- FastMCP Apps: <https://gofastmcp.com/apps/overview>
- FastMCP App backend interaction: <https://gofastmcp.com/apps/fastmcp-app>
- FastMCP generative UI: <https://gofastmcp.com/apps/generative>
- Official ext-apps package: <https://github.com/modelcontextprotocol/ext-apps>

FastMCP is a productivity framework, not the protocol authority. Its package
major is independent of the MCP core revision. Pin FastMCP and Prefab, run Tier
1 SDK conformance tests, and treat model-generated UI code as a separate
security boundary.

### Authorization Standards

- OAuth 2.1 draft and updates: <https://oauth.net/2.1/>
- OAuth Resource Indicators, RFC 8707: <https://www.rfc-editor.org/rfc/rfc8707>
- OAuth Protected Resource Metadata, RFC 9728: <https://www.rfc-editor.org/rfc/rfc9728>
- JWT access token profile, RFC 9068: <https://www.rfc-editor.org/rfc/rfc9068>
- OpenID Connect Core: <https://openid.net/specs/openid-connect-core-1_0.html>

Bind tokens to the intended resource and audience. Do not forward upstream
bearer tokens to downstream services unless explicit token exchange and policy
permit it.

## PyTorch And Distributed Training

- PyTorch documentation: <https://docs.pytorch.org/docs/stable/>
- Distributed overview: <https://pytorch.org/tutorials/beginner/dist_overview.html>
- DistributedDataParallel: <https://docs.pytorch.org/docs/stable/generated/torch.nn.parallel.DistributedDataParallel.html>
- FSDP2 `fully_shard`: <https://docs.pytorch.org/docs/main/distributed.fsdp.fully_shard.html>
- FSDP1: <https://docs.pytorch.org/docs/stable/fsdp.html>
- DTensor: <https://docs.pytorch.org/docs/stable/distributed.tensor.html>
- Tensor parallelism: <https://docs.pytorch.org/docs/stable/distributed.tensor.parallel.html>
- Pipeline parallelism: <https://docs.pytorch.org/docs/stable/distributed.pipelining.html>
- Distributed Checkpoint: <https://docs.pytorch.org/docs/stable/distributed.checkpoint.html>
- Activation checkpointing: <https://docs.pytorch.org/docs/stable/checkpoint.html>
- Automatic mixed precision: <https://docs.pytorch.org/docs/stable/amp.html>
- `torch.compile`: <https://docs.pytorch.org/docs/stable/generated/torch.compile.html>
- PyTorch profiler: <https://docs.pytorch.org/docs/stable/profiler.html>

Check API stability labels. Pin PyTorch, CUDA, NCCL, drivers, kernels, model code,
and checkpoint format together. Test distributed recovery, not only throughput.

## Hugging Face Model And Training Tooling

- Transformers: <https://huggingface.co/docs/transformers/>
- Transformers Trainer: <https://huggingface.co/docs/transformers/main_classes/trainer>
- Chat templates: <https://huggingface.co/docs/transformers/chat_templating>
- Accelerate: <https://huggingface.co/docs/accelerate/>
- Accelerate distributed training: <https://huggingface.co/docs/accelerate/usage_guides/distributed_training>
- Accelerate FSDP: <https://huggingface.co/docs/accelerate/usage_guides/fsdp>
- PEFT: <https://huggingface.co/docs/peft/>
- PEFT LoRA: <https://huggingface.co/docs/peft/developer_guides/lora>
- PEFT quantization and QLoRA: <https://huggingface.co/docs/peft/developer_guides/quantization>
- TRL: <https://huggingface.co/docs/trl/>
- BitsAndBytes: <https://huggingface.co/docs/transformers/quantization/bitsandbytes>
- Safetensors: <https://huggingface.co/docs/safetensors/>
- Datasets: <https://huggingface.co/docs/datasets/>

Verify the compatibility matrix among Transformers, Accelerate, PEFT, TRL,
PyTorch, DeepSpeed, model code, tokenizer, chat template, and checkpoint format.

## Large-Model Training Frameworks

- DeepSpeed: <https://www.deepspeed.ai/getting-started/>
- DeepSpeed ZeRO: <https://www.deepspeed.ai/tutorials/zero/>
- NVIDIA Megatron Core: <https://docs.nvidia.com/megatron-core/developer-guide/latest/>
- NVIDIA NeMo Framework: <https://docs.nvidia.com/nemo-framework/user-guide/latest/>
- NVIDIA Transformer Engine: <https://docs.nvidia.com/deeplearning/transformer-engine/user-guide/>
- Kubeflow Trainer: <https://www.kubeflow.org/docs/components/trainer/>
- Ray Train: <https://docs.ray.io/en/latest/train/train.html>

Use these after a simpler PyTorch DDP or FSDP2 design fails a measured memory,
throughput, duration, recovery, or model-support gate.

## Hardware And Nebius AI Cloud

- Nebius GPU VM types: <https://docs.nebius.com/compute/virtual-machines/types>
- Nebius GPU clusters: <https://docs.nebius.com/compute/clusters/gpu>
- Nebius GPU topology: <https://docs.nebius.com/compute/clusters/gpu/topology>
- Nebius Compute pricing: <https://docs.nebius.com/compute/resources/pricing>
- NVIDIA H100: <https://www.nvidia.com/en-us/data-center/h100/>
- NVIDIA H200: <https://www.nvidia.com/en-us/data-center/h200/>
- NVIDIA HGX B200 and B300: <https://www.nvidia.com/en-us/data-center/hgx/>
- NVIDIA NCCL: <https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/>

Verify region, quota, reservation, memory, interconnect, NIC mapping, storage,
image, driver, and price. Run NCCL and target-workload tests on the intended
cluster topology.

## Inference Engines And Serving

- vLLM: <https://docs.vllm.ai/en/latest/>
- vLLM supported models: <https://docs.vllm.ai/en/latest/models/supported_models.html>
- vLLM quantization: <https://docs.vllm.ai/en/latest/features/quantization/>
- vLLM tool calling: <https://docs.vllm.ai/en/latest/features/tool_calling.html>
- vLLM structured outputs: <https://docs.vllm.ai/en/latest/features/structured_outputs.html>
- vLLM speculative decoding: <https://docs.vllm.ai/en/latest/features/spec_decode/>
- SGLang: <https://docs.sglang.ai/>
- NVIDIA TensorRT-LLM: <https://nvidia.github.io/TensorRT-LLM/>
- TensorRT-LLM quantization: <https://nvidia.github.io/TensorRT-LLM/features/quantization.html>
- TensorRT-LLM disaggregated serving: <https://nvidia.github.io/TensorRT-LLM/features/disagg-serving.html>
- KServe: <https://kserve.github.io/website/>
- Ray Serve LLM: <https://docs.ray.io/en/latest/serve/llm/>
- NVIDIA Triton Inference Server: <https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/>

Verify exact model, tokenizer, template, adapter, quantization, runtime, kernel,
hardware, driver, API, and optimization compatibility. Benchmark the target
request distribution and quality suite.

## Retrieval, Embeddings, And AI Data

- OpenAI embeddings: <https://developers.openai.com/api/docs/guides/embeddings>
- BGE-M3 model card: <https://huggingface.co/BAAI/bge-m3>
- pgvector: <https://github.com/pgvector/pgvector>
- PostgreSQL row security: <https://www.postgresql.org/docs/current/ddl-rowsecurity.html>
- PostgreSQL full-text search: <https://www.postgresql.org/docs/current/textsearch.html>
- Qdrant: <https://qdrant.tech/documentation/>
- OpenSearch vector search: <https://docs.opensearch.org/latest/vector-search/>
- Neo4j GraphRAG: <https://neo4j.com/docs/neo4j-graphrag-python/current/>
- LlamaIndex: <https://developers.llamaindex.ai/python/framework/>
- NVIDIA NeMo Curator: <https://docs.nvidia.com/nemo/curator/latest/>
- Apache Arrow: <https://arrow.apache.org/docs/>
- Apache Parquet: <https://parquet.apache.org/docs/>

Verify dimension, normalization, metric, filters, deletes, consistency,
multitenancy, backup, and client behavior. Benchmark on the target corpus with
production authorization filters.

## Lifecycle, Evaluation, And Observability

- MLflow Tracking: <https://mlflow.org/docs/latest/ml/tracking/>
- MLflow Model Registry: <https://mlflow.org/docs/latest/ml/model-registry/>
- MLflow GenAI evaluation: <https://mlflow.org/docs/latest/genai/eval-monitor/>
- MLflow tracing: <https://mlflow.org/docs/latest/genai/tracing/>
- MLflow OpenTelemetry integration: <https://mlflow.org/docs/latest/genai/tracing/opentelemetry/>
- OpenTelemetry concepts: <https://opentelemetry.io/docs/concepts/>
- OpenTelemetry trace context: <https://www.w3.org/TR/trace-context/>
- OpenTelemetry GenAI conventions: <https://opentelemetry.io/docs/specs/semconv/gen-ai/>
- OpenTelemetry MCP conventions: <https://opentelemetry.io/docs/specs/semconv/>

Semantic conventions may be experimental. Pin the convention version and apply
explicit data minimization, redaction, retention, and access policy.

## Safety And Security

- OWASP Top 10 for LLM Applications: <https://genai.owasp.org/llm-top-10/>
- OWASP MCP Top 10: <https://owasp.org/www-project-mcp-top-10/>
- NVIDIA NeMo Guardrails: <https://docs.nvidia.com/nemo/guardrails/latest/>
- NIST AI Risk Management Framework: <https://www.nist.gov/itl/ai-risk-management-framework>
- SLSA supply-chain levels: <https://slsa.dev/>
- Sigstore: <https://docs.sigstore.dev/>

Use deterministic authentication, authorization, isolation, schemas, budgets,
approvals, idempotency, sandboxing, and side-effect controls outside model
prompts and probabilistic guardrails.

## Research Record

For each material claim, record:

```markdown
Claim: <exact behavior>
Product or standard: <identity>
Version or revision: <exact>
Official source: <URL and section>
Source date or release: <date or version>
Evidence state: Officially documented | Measured | Assumed
Target compatibility test: <test or reason not required>
Decision impact: <how the claim changes selection>
Revalidation trigger: <release, model, host, policy, or environment change>
```
