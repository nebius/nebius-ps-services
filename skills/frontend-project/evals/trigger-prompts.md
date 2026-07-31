# Trigger Prompts

Static examples do not prove runtime activation.

## Should Trigger

```text
Create a React, TypeScript, and Vite frontend project with strict type checking
and unit tests. Do not install dependencies.
```

```text
Standardize this existing React/Vite package while preserving its current
source and lockfile.
```

```text
Use $frontend-project in coordinated-candidate scope for apps/web and return
candidate files to the supplied private scaffold bundle.
```

```text
Materialize this approved React/Vite frontend assignment with a typed public
VITE_API_ORIGIN contract, React Router, Oxlint, and Prettier. Do not install
dependencies or write the target.
```

## Should Not Trigger

```text
Choose between React, Vue, and server-rendered HTML for this product.
```

Use `app-stack`.

```text
Create a Node.js API service.
```

Node backend scaffolding is unsupported by this skill.

```text
Add Docker and Compose to this React application.
```

Use `container`.

```text
Build and host this website with OpenAI Sites.
```

Use the Sites workflow.

## Quality Checks

- Uses only React, TypeScript, and Vite.
- Resolves exact compatible versions from approved input or official docs.
- Never runs a native generator or installs dependencies by default.
- Separates standalone and coordinated-candidate ownership.
- In coordinated mode, emits deterministic candidates only for the exact
  assigned frontend root and returns profile/input/file digests plus bound
  validations.
- Generates only public `VITE_*` names with empty example values and rejects
  secret-like names, separator-normalized API/access-key markers, or supplied
  values.
- Accepts only npm, pnpm, Yarn, or Bun as the approved package manager.
- Adds routing, lint, and format files only when their approved profiles and
  exact versions are supplied.
- Never claims root CI, ignore, container, infrastructure, Helm, or agent files.
- Reports lockfile, dependency-backed tests, and builds as pending when unrun.
