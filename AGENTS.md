# OpsPilot agent contract

## Mission

Implement OpsPilot as a small, polished, technically credible student project. Finish the phase or construction pass explicitly assigned by the user. A pass may contain several named phases; execute them sequentially and do not cross its boundary.

## Source priority

When instructions conflict, use this order:

1. The user's current instruction.
2. The current phase document in `docs/phases/`.
3. `docs/PRODUCT-SPEC.md`, `docs/ARCHITECTURE-CONTRACT.md`, `docs/CONTEXT-CONTRACT.md`, and `docs/DEVELOPMENT-CONTEXT.md`.
4. `docs/UI-SPEC.md`, `docs/BUG-RISK-REGISTER.md`, and `docs/TEST-MATRIX.md`.
5. Existing implementation and older prose documentation.

Report unresolved conflicts. Do not combine contradictory requirements by inventing a third design.

## Phase execution loop

1. Read this file and the assigned pass boundary. Load only the current phase and shared documents listed under that phase's **Required context**.
2. Inspect relevant files and uncommitted changes. Preserve user work.
3. Confirm the entry gate. Repair only a smallest blocking prerequisite and record it.
4. Implement every deliverable. Leave no placeholder behavior, fake success, dead control, or untracked `TODO`.
5. Exercise happy, empty, error, and named-risk paths.
6. Run the exact exit gate. Fix failures caused by the phase.
7. Update `docs/BUILD-STATUS.md` and actual-state docs only when evidence changes them.
8. If the assigned pass contains another phase, write the compact phase evidence to `docs/BUILD-STATUS.md`, discard detailed transcripts from working context, then load the next phase. Otherwise stop with a concise report.

## Development context discipline

- Start from the current task, affected module, direct dependencies, public contracts, relevant tests, and then configuration. Expand outward only when evidence crosses a boundary or the first approach fails.
- Use targeted `rg`/file reads. Do not dump the repository, full documentation set, full logs, generated artifacts, or every service into context.
- Before changing a shared interface, search all importers, callers, implementations, contract tests, and service consumers. Context minimization must not become context blindness.
- Reproduce a bug before editing. Make the smallest coherent fix and run the smallest meaningful check first. Do not refactor nearby working modules during a focused fix.
- Update only affected documentation during implementation. Phase 16 is the deliberate exception that reviews the full documentation set.
- Preserve architecture awareness through contracts and `BUILD-STATUS.md`; do not reconstruct it by rereading the whole repository.

## Implementation rules

- Use Python 3.11 unless a recorded decision changes it.
- Keep domain logic independent of HTTP, environment globals, and framework objects.
- Use typed Pydantic models at process boundaries and type hints internally.
- Treat user input, retrieved documents, tool output, model output, and configuration as untrusted.
- Pass typed, consumer-specific context projections between components. Never pass complete graph state, database rows, documents, prompts, or unrelated metadata for convenience.
- A new context field requires a reason in `docs/CONTEXT-CONTRACT.md` and an isolation-test review.
- Never hard-code the demonstration answer or report unavailable evidence as present.
- Never expose secrets, stack traces, prompts, SQL, or raw provider payloads to the browser.
- External calls require explicit timeouts and bounded retries. Do not retry validation or authentication failures.
- Mark fallbacks in structured metadata. Degraded operation must not masquerade as full success.
- Keep optional training and MLflow dependencies out of the normal runtime.
- Add no infrastructure or library outside the specification without a concrete need and recorded decision.
- Avoid giant files. A file over roughly 400 lines triggers a cohesion review, not an automatic split.

## UI rules

- `docs/UI-SPEC.md` is a behavior contract.
- Every interactive operation has idle, loading, success, and error behavior.
- Render and inspect desktop and mobile states before completing UI work.
- A successful build or DOM assertion alone does not validate appearance.
- Use semantic HTML, visible keyboard focus, reduced-motion support, and no horizontal page scrolling at 320 CSS pixels.
- Add no decorative charts, dashboards, themes, or animation beyond the UI specification.

## Testing rules

- Tests run without paid API credentials by substituting deterministic fakes at provider interfaces.
- Do not weaken assertions to make broken behavior pass.
- Test observable contracts and important failures, not framework internals.
- Run focused tests while editing, then the phase exit gate.
- If a required tool is unavailable, run the closest deterministic check and record exactly what remains unverified.

## Dependency rules

Before selecting LangChain, LangGraph, MCP, PEFT, MLflow, pgvector, FastAPI, or Pydantic APIs, check current official documentation and compatibility. Pin exact resolved versions. Record the verified combination in `docs/development-notes.md`. Do not copy older tutorial APIs without verification.

## Completion language

"Complete" means every deliverable exists, the exit gate passes, and no severity-1 or severity-2 risk introduced by the phase is open. Never claim "bug free." State what was tested and what remains environment-dependent.
