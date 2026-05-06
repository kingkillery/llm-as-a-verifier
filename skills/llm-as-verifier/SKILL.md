---
name: llm-as-verifier
description: Use the local llm-as-a-verifier tool to collect and rank evidence-aware coding-agent trajectories. Trigger when a user asks to use llm-as-a verifier, verify a coding-agent run, compare candidate runs, capture objective command/diff/final-answer evidence, or gate completion on a verifier result using the tool at C:\dev\Desktop-Projects\llm-as-a-verifier.
---

# LLM as Verifier

When this skill is triggered, use the actual `llm-as-a-verifier` CLI from `C:\dev\Desktop-Projects\llm-as-a-verifier`. Do not simulate, paraphrase, or manually recreate the verifier. The job is to collect evidence with `verifier-collect`, run `verify-trajectories`, inspect the JSON result, and let that result influence whether the task is complete.

Use `llm-as-a-verifier` as an evidence logger and deterministic verifier for coding-agent work. It is not a replacement for tests or review; it checks whether final success claims are supported by captured command output, diffs, and other trajectory evidence.

## Fast Path

First, check whether the CLI is already available:

```powershell
verifier-collect --help
verify-trajectories --help
```

Install once if those commands are missing:

```powershell
python -m pip install --user -e C:\dev\Desktop-Projects\llm-as-a-verifier
```

Then confirm the CLI:

```powershell
verifier-collect --help
verify-trajectories --help
evaluate-verifier --help
```

Initialize from the repo being worked on:

```powershell
verifier-collect init `
  --task-id <short-task-id> `
  --task-description "<one-sentence task>" `
  --candidate-id <candidate-id> `
  --profile node `
  --out .verifier\trajectory.json
```

Choose the closest profile: `default`, `python`, `node`, `webapp`, `security-sensitive`, or `browser-agent`.

## Collect Evidence

Use the real command wrapper for meaningful verification commands:

```powershell
verifier-collect run --file .verifier\trajectory.json -- npm test
verifier-collect run --file .verifier\trajectory.json -- npm run build
```

Capture the patch:

```powershell
verifier-collect diff --file .verifier\trajectory.json
```

Record the final claim exactly enough for the verifier to check:

```powershell
verifier-collect final --file .verifier\trajectory.json "Implemented the requested change. npm test and npm run build pass."
```

Run the verifier:

```powershell
verify-trajectories .verifier\trajectory.json --out .verifier\verification_result.json
```

Read `.verifier\verification_result.json` before finalizing. Use the ranked candidate, score, confidence, positive and negative evidence, unsupported claims, missing evidence, risk factors, uncertainty reasons, and semantic certificate to decide whether more work is needed.

If the CLI command fails because it is not installed, install it from the local path above and retry. If it still fails, report the failure and do not claim that LLM-as-a-verifier was run.

## Multiple Candidates

For competing runs, collect each candidate under the same task with a distinct `candidate_id`. Then run:

```powershell
verify-trajectories .verifier\trajectory.json --out .verifier\verification_result.json
```

Prefer the top-ranked candidate only when its evidence supports the task. If scores are close or confidence is low, inspect pairwise comparisons and missing evidence before choosing.

## Result Policy

Treat `verification_result.json` as a gate, not a rubber stamp:

- If the verifier reports contradicted final claims, nonzero command exits, missing tests, suspiciously small diffs, or unsupported success claims, fix or gather better evidence before finalizing.
- If the verifier passes but important manual risks remain, mention those risks in the final answer.
- If the verifier cannot run, report that clearly and fall back to normal verification commands.
- Do not claim success solely because the final assistant message says the work is done.

## Repo Config

`verifier-collect init` creates `.verifier\verifier.toml`. Commit it only when stable repo-specific verifier policy is useful.

When editing TOML on Windows, quote filesystem paths with single quotes:

```toml
tool_path = 'C:\dev\Desktop-Projects\llm-as-a-verifier'
```

Use double quotes only when backslashes are escaped:

```toml
tool_path = "C:\\dev\\Desktop-Projects\\llm-as-a-verifier"
```

## Final Answer

When this skill is used, include a short verifier status:

```text
Verifier: ran verify-trajectories; top candidate <id>, score <score>, confidence <confidence>.
```

If verification was blocked, state the blocker and the strongest evidence that was still checked.
