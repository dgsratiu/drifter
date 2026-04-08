# Context: Structured Intents Without Killing Emergence

## Problem

Drifter needs to work reliably with weaker models that can often produce valid structured output but are unreliable at protocol-sensitive tool use such as shell commands, CLI argument ordering, quoting, and multi-step side effects. At the same time, the system should preserve emergent behavior instead of collapsing agent sessions into rigid form-filling workflows.

The dream-cycle failure made this concrete: the model successfully reasoned and wrote useful artifacts, but the required `drifter post` call failed because `#dreams` was interpreted as a shell comment. The issue was not cognition; it was the reliability of the execution boundary.

## Core Principle

Constrain effects, not thought.

The system should preserve open-ended reasoning, exploration, and agentic tool use during the main body of a session, while making required system effects pass through a narrow structured interface that the harness or kernel can execute deterministically.

This creates a split:

1. **Open-ended cognition**
- reasoning
- synthesis
- discovery
- coding
- exploratory tool use

2. **Structured intent**
- requested posts
- requested file materialization
- requested watch updates
- requested proposals
- requested commits or other protocol-sensitive effects

3. **Deterministic execution**
- harness validates the structured output
- harness performs the side effects
- harness records success/failure against real system state

## Design Direction

### Main session model

For the primary full-tool session, keep normal coding-agent behavior intact.

The model should still be able to:
- inspect the codebase
- run commands
- read logs
- write code
- test
- reason broadly
- discover unplanned opportunities

At the end of the session, require a final machine-readable JSON block that describes outcomes and requested effects. This acts as a reconciliation footer rather than a replacement for the main session behavior.

The body stays emergent. The footer is structured.

### Why this preserves emergence

The model is not forced to serialize its whole reasoning process into schema. Only the protocol boundary is typed. The JSON footer should describe outcomes and requested effects, not chain-of-thought.

Good examples:
- files written
- bus posts requested
- tensions updated
- proposals requested
- watches to add/remove
- commit requested
- blockers
- short final summary

Bad examples:
- full reasoning transcript
- forced decomposition of all thinking into schema fields

This means strong models keep their flexibility, while weaker models can still be made reliable at the handoff boundary.

## Session Pattern

### Interactive execution plane

During the session:
- unrestricted reasoning
- unrestricted tool use where appropriate
- iterative coding/debugging/testing

### Declarative reconciliation plane

At the end:
- emit a structured JSON block
- harness verifies the claims against filesystem/git/bus state
- harness performs deferred or required side effects deterministically
- harness can reject inconsistent completions

This makes the system more robust to weak-model execution failures without flattening the agent into a pure form emitter.

## Dream-Cycle Application

Dreams are the first concrete use case:
- model should produce semantic outputs
- harness should own canonical file writes and required bus posts

Long-term direction:
- model emits a structured `DreamResult`
- harness writes the dream artifact to a canonical path
- harness writes `tensions.md`
- harness writes session handoff
- harness posts the dream summary to `dreams`
- harness updates state only after deterministic success

This avoids filename ambiguity, shell quoting failures, and partial completion being mistaken for success.

## General Rule

Use the LLM for:
- interpretation
- prioritization
- summarization
- proposal generation
- drafting content

Use the harness/kernel for:
- canonical file writes
- bus posts
- watch/unwatch changes
- proposal submission
- commits
- notifications
- any side effect with a strict correctness contract

If an action is required for system correctness and its parameters can be represented in a schema, prefer structured intent plus harness execution over direct model-issued tool calls.

## Guardrail Against Over-Constraint

Do not require the entire response to be structured.

Instead:
- allow freeform reasoning in the body
- require a minimal structured footer for required effects
- provide an escape hatch for findings that do not fit the schema

Possible escape fields:
- `open_questions`
- `unstructured_findings`
- `needs_human_decision`
- `cannot_express_structurally`

This preserves novel or unexpected insights instead of forcing them into a brittle protocol.

## Implementation Strategy

1. Keep the current main session fully agentic.
2. Add a minimal final JSON footer schema.
3. Use that footer as the deterministic reconciliation boundary.
4. Start with dream cycles as the first structured-intent path.
5. Expand to regular-cycle posts, proposals, watch updates, and other protocol-sensitive actions only after observing real behavior.

## Architectural Framing

Treat structured output as an ABI between intelligence and infrastructure.

- Prompts and reasoning stay flexible.
- The structured footer is the stable contract.
- The harness is the executor and validator.

This makes model quality less tightly coupled to system correctness and gives Drifter a path to use weaker models without losing the benefits of emergent behavior.
