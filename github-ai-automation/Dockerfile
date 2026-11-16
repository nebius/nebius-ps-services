# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d AS codex
ARG CODEX_VERSION
ARG CODEX_INTEGRITY
RUN test -n "$CODEX_VERSION" && test -n "$CODEX_INTEGRITY" \
    && CODEX_VERSION="$CODEX_VERSION" CODEX_INTEGRITY="$CODEX_INTEGRITY" node --input-type=module -e \
    'import fs from "node:fs"; import crypto from "node:crypto"; const v=process.env.CODEX_VERSION; if(!/^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$/.test(v))throw Error("version"); const r=await fetch("https://registry.npmjs.org/@openai/codex/-/codex-"+v+".tgz",{redirect:"error"}); if(!r.ok)throw Error("download"); const b=Buffer.from(await r.arrayBuffer()); if("sha512-"+crypto.createHash("sha512").update(b).digest("base64")!==process.env.CODEX_INTEGRITY)throw Error("integrity"); fs.writeFileSync("/tmp/codex.tgz",b);' \
    && npm install --global --ignore-scripts /tmp/codex.tgz \
    && npm cache clean --force && rm /tmp/codex.tgz

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
RUN apt-get update && apt-get install --yes --no-install-recommends git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 agent && useradd --uid 10001 --gid 10001 --create-home agent
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /usr/local/lib/node_modules/@openai/codex /opt/codex
RUN ln -s /opt/codex/bin/codex.js /usr/local/bin/codex
COPY src/github_ai_automation/relay.py /opt/relay.py
USER 10001:10001
WORKDIR /home/agent
LABEL org.opencontainers.image.title="GitHub AI automation sandbox" \
      org.opencontainers.image.description="Finite non-root Codex worker and restricted inference relay"
ENTRYPOINT []
CMD ["codex", "--version"]
