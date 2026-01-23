# Generate Workflow SVG

This project ships a Graphviz workflow diagram and a helper script that renders
the SVG and embeds a user icon so the output is fully self-contained.

## What the script does

`image/render-workflow-svg.py`:

1. Runs Graphviz to render `image/nebius-acc-workflow.dot` into
   `image/nebius-acc-workflow.svg`.
2. Downloads a public user icon.
3. Embeds the icon into the SVG as a `data:` URI so no external image files are needed at view time.

## How to run

From the repo root:

```bash
python image/render-workflow-svg.py
```

## Do I still need the `dot` command?

No. The script already runs:

```bash
dot -Tsvg image/nebius-acc-workflow.dot -o image/nebius-acc-workflow.svg
```

Only run the `dot` command directly if you intentionally want a plain SVG without the embedded icon.
