# Three-Tier Live Process

Use this process only after explicit `--create`, `--create --keep`, or
`--resume`:

1. Read and follow `references/three-tier-live.md`. Confirm Docker Engine,
   Docker Compose, Git, installed-source skill parity, canonical Google Chrome,
   and the `computer-use` capability before live mutation. The lifecycle helper
   must launch Chrome directly with a fresh verifier-owned user-data directory,
   new process group, and verification-ID window marker; never use or close an
   existing Chrome instance. Prove Computer Use with a successful real
   `get_app_state` that exposes the exact marker before every action. Tool or
   process discovery alone is not proof. Missing required live capability
   before an attempt is PARTIAL. A failed attempted action is FAIL.
2. For create modes, prepare one owned lifecycle with
   `scripts/three_tier_lifecycle.py`. Its `prepare` action serializes lifecycle
   changes, destroys the previous active environment through the same exact
   ownership-checked cleanup path as standalone destroy, and only then creates
   a fresh verification ID and project. If ownership, project safety, or
   cleanup cannot be proven, stop without creating a replacement. Cleanup must
   canonicalize and deduplicate every recorded/discovered Docker alias by
   resource identity after validating both exact ownership labels, covering a
   prior run interrupted before inventory capture. Its cumulative ledger must
   survive failed retries, and an already-absent resource counts as success only
   when a fresh inspect proves absence. Retain the returned verification ID as
   this invocation's immutable
   generation fence. Pass it as `--expected-verification-id` to every later
   mutating helper action; never refresh it from a newer status response. Run
   the helper's `prepare-images` action to pull only the fixed
   public base images through an owned empty Docker CLI config; do not reuse
   that config for Compose. For resume mode, require the existing lifecycle to
   be owned, KEPT, and previously FAIL or PARTIAL; revalidate its project
   boundary and recorded resources without creating replacements. Use the
   private root's isolated agent home for all prompt workspace and phase state;
   never reuse or delete the user's ordinary Agentic SDLC run directory.
3. Create the project through the normal prompt-bound Agentic SDLC workflow:
   first run `$sdlc-start workspace init <project-folder>`, then use
   `scripts/render_three_tier_prompt.py` to replace the generated starter body
   while preserving its managed identity, and finally run
   `$sdlc-start run <prompt-ref-or-file>`. Follow the returned phase
   skill; do not make the lifecycle helper or hooks orchestrate phases.
4. Build all three logical layers: browser GUI, Django/Gunicorn web/API server,
   and PostgreSQL. Run exactly two labelled Compose containers, dynamically
   publish only the web port on loopback, and keep PostgreSQL private to the
   Compose network. Run every Compose action through the helper's
   generation-locked `run-compose` action with the immutable expected
   verification ID; never invoke a mutating `docker compose` command directly.
   This makes a replacement wait for an in-flight action and prevents a
   superseded invocation from starting or changing a stack. Record containers
   with the role-specific `--web-container` and `--database-container`
   arguments; the helper verifies Compose service labels before accepting them.
5. Execute every phase and test class in the scenario reference. Local ship
   means build and run the promoted clean SHA locally; it never means push,
   publish, PR creation, or PR merge.
6. For GUI evaluation and UAT, explicitly route through `sdlc-gui-test` with
   `harness: computer-use`. Immediately before the first navigation in
   `sdlc-evaluate`, and again immediately before `sdlc-uat-tests`, require a
   fresh successful `get_app_state` for the exact selected browser. Unless the
   current agent surface explicitly confirms locked Computer Use is enabled for
   this session, the host must be unlocked. A normal browser window must be
   visible, unminimized, foreground, and on the current macOS Space.
   Lock/unlock, display, Space, or browser-window changes invalidate earlier
   readiness. Refresh accessibility state after every successful action.
   Correlate GUI observations with
   independent API and PostgreSQL results and prove persistence across a
   service restart.
7. If a just-in-time capture returns `cgWindowNotFound` or another visibility
   failure, record `ENVIRONMENT_DEFECT` with the explicit stage
   `pre-navigation-window-capture` and state that no GUI navigation or action
   was attempted. Record only bounded sanitized diagnostics: selected browser,
   whether lock/window visibility/frontmost/current-Space state is known, and
   whether the call returned an error or timed out. If a Computer Use call
   hangs or times out, or fresh capture loses responses, treat
   the shared service as unhealthy and stop all further Computer Use calls for
   that attempt. Do not use the same path for `list_apps`, new-window recovery,
   repeated browser retries, browser restart, or service restart. Exact owned
   process cleanup remains available without Computer Use. Fresh-session or
   service recovery remains a separate explicitly authorized action.
8. Persist semantic results incrementally using
   `agentic-sdlc/three-tier-results-v2`. Failed runs retain validated partial
   layer/test status; the lifecycle helper derives PASS from
   required phases, tests, Git identity, GUI actions, API/database correlation,
   restart persistence, and distinct artifacts. Record canonical per-phase JSON
   results and the three structured Computer Use readiness stages. Never accept
   placeholder `{"result":"pass"}` evidence or screenshots as the only oracle.
9. Write the complete sanitized report with layer inventory, resolved ports
   and endpoints, baseline/promoted SHAs, phase/test/UAT outcomes, exact owned
   resource IDs, recorded validation commands, top issues and recommended
   fixes, and cleanup/retention result.
10. With `--keep`, preserve the owned project, private SDLC state/evidence, two
   running containers, network, database volume, built web image, and exact
   verifier-owned Chrome instance/profile; report `KEPT` and the later destroy
   invocation. Without `--keep`, revalidate and close only its recorded process
   group, then destroy every exact owned live resource in a finally-style path.
   Browser or Docker identity ambiguity persists resumable `CLEANUP_FAILED`
   state before ambiguous mutation. Any cleanup failure makes the result FAIL.
