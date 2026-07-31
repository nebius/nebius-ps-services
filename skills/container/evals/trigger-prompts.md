# Trigger Prompts

Static examples validate metadata boundaries; they do not prove runtime
activation.

## Should Trigger

```text
Create a production container image for this FastAPI service and validate
graceful shutdown.
```

```text
Review the Dockerfile and Compose stack for production readiness.
```

```text
Reduce this image size and build duration without changing runtime behavior.
```

```text
Make this image compatible with non-root execution and a read-only root
filesystem.
```

```text
The container works locally but exits immediately in Kubernetes. Find the
container or runtime-contract root cause.
```

```text
Build and validate this service for AMD64 and ARM64 from my ARM64 Mac.
```

```text
Create a local integration stack for the API, PostgreSQL, and Redis.
```

```text
Review this CUDA inference container for Nebius GPU nodes.
```

```text
Add SBOM and provenance requirements to this image build.
```

```text
Use $container in coordinated-candidate scope for the Dockerfile,
.dockerignore, and compose.test.yaml paths assigned by the scaffold plan.
```

## Should Not Trigger

```text
Should this application use Kafka or Redpanda?
```

Use `app-stack`.

```text
Create a new Python package.
```

Use `python-project`.

```text
Write a GitHub Actions workflow to build and publish this image.
```

Use `github-workflows`; `container` may provide build and evidence
requirements.

```text
Publish version 1.4.0 of this image.
```

Use `publish-image`.

```text
Create a Helm chart for this service.
```

Use `helmchart`; `container` may provide the runtime handoff contract.

```text
Start my disposable Ubuntu VS Code container.
```

Use `attach-ubuntu`.

```text
Choose between Kubernetes, a managed container service, and one Docker host.
```

Use `app-stack` or `design`; the deployment architecture is not approved.

## Quality Checks

- Classifies the workload before applying production controls.
- Separates host, builder, target, and runtime-validation platforms.
- Preserves the typed Python/React/Vite scaffold renderer without treating it
  as a universal template.
- Keeps general Compose audits separate from the closed scaffold validator.
- Requires explicit opt-in for builds, runs, pulls, network, supply-chain
  tools, and GPU devices.
- Redacts values and bounds command output.
- Never claims Helm, Kubernetes, GitHub Actions, publication, signing, or
  deployment ownership.
- Marks unsupported or untested platforms and evidence as unvalidated.
