# LLM-as-a-Verifier: Evidence-Aware Trajectory Verification

LLM-as-a-Verifier provides a small, deterministic verifier for complete
tool-using coding-agent trajectories. It ingests one or more candidate runs for
the same task, extracts objective evidence, scores and ranks candidates, and
explains each ranking with cited evidence IDs.

The core bet is not raw model intelligence. The tool is built around better
verification, better trajectory selection, better uncertainty calibration, and
execution-grounded judging. Final assistant claims are treated as claims to
check, not as proof of success.

The intended workflow is install once, then use from any receiving codebase.
Repo-specific behavior lives in `.verifier/verifier.toml` inside the receiving
repo, not in this verifier repo.

## Quickstart

Install the verifier globally/user-wide from this repo:

```powershell
python -m pip install --user -e C:\dev\Desktop-Projects\llm-as-a-verifier
```

Confirm the commands are available:

```powershell
verifier-collect --help
verify-trajectories --help
evaluate-verifier --help
```

Bootstrap verifier state inside a receiving repo:

```powershell
cd C:\Dev\pk-tool
verifier-collect init `
  --task-id pk-tool-fix-cli `
  --task-description "Fix the CLI crash when --config is omitted." `
  --candidate-id run_a `
  --profile python `
  --out .verifier\trajectory.json
```

This creates:

```text
.verifier\trajectory.json
.verifier\verifier.toml
```

Collect evidence while the agent works:

```powershell
verifier-collect run --file .verifier\trajectory.json -- pytest
verifier-collect diff --file .verifier\trajectory.json
verifier-collect run --file .verifier\trajectory.json -- pytest
verifier-collect final --file .verifier\trajectory.json "Implemented the fix and pytest passes."
```

Rank the collected candidate:

```powershell
verify-trajectories .verifier\trajectory.json --out .verifier\verification_result.json
```

Open `.verifier\verification_result.json` to see the ranked candidates,
calibrated score, confidence, positive and negative evidence, failure modes,
missing evidence, unsupported claims, risk factors, uncertainty reasons, and
cited evidence.

## Command Summary

`verifier-collect run` records the command, exit code, stdout, and stderr.
`verifier-collect diff` records the current `git diff`. `verifier-collect final`
records the final assistant claim so the verifier can check whether it is
supported or contradicted by objective evidence.

`verify-trajectories` reads a trajectory JSON file and writes a structured
ranking result.

`evaluate-verifier` reads a labeled dataset and reports top-1 accuracy,
pairwise ranking accuracy, calibration error and bins, false-positive rate,
false-negative rate, citation validity, and unsupported-success detection.

## Repo-Local Config

`.verifier\verifier.toml` is the repo-local policy layer. Commit it if you want
stable, project-specific verifier behavior. The globally installed verifier
loads
`.verifier\verifier.toml` from the current repo by default when you run
`verify-trajectories` or `evaluate-verifier`.

Profiles are built-in policy presets inspired by useful `pk-skills1` patterns:

| Profile | Use For | Imported Pattern |
|---|---|---|
| `default` | Generic coding tasks | Evidence-first verifier baseline |
| `python` | Python packages and CLIs | Debugging and test/build discipline |
| `node` | JS/TS packages | Code review plus test/build detection |
| `webapp` | Frontend apps | Webapp testing risk signals |
| `security-sensitive` | Auth, secrets, user input, dependencies | Security-review checks |
| `browser-agent` | Browser automation and UI trajectories | Browser trace / console / network risk signals |

Example config:

```toml
verifier_version = '0.1.0'
profile = 'python'
test_commands = ['pytest', 'npm test', 'pnpm test', 'yarn test', 'cargo test', 'go test']
build_commands = ['ruff', 'mypy', 'pyright', 'python -m compileall', 'flake8']
source_extensions = ['.c', '.cc', '.cpp', '.cs', '.go', '.h', '.hpp', '.java', '.js', '.jsx', '.kt', '.php', '.py', '.rb', '.rs', '.scala', '.swift', '.ts', '.tsx']
test_path_keywords = ['test', 'spec']
risk_patterns = ['subprocess\.', 'os\.system', 'eval\(', 'exec\(']
missing_tests_penalty = 0.12
tests_only_change_penalty = 0.15
close_case_threshold = 0.10
```

`verifier-collect init --profile python|node|webapp|security-sensitive|browser-agent`
writes the selected profile into `.verifier\verifier.toml`. You can edit the
generated arrays afterward for repo-specific commands, extensions, and risk
patterns.

You can override config explicitly:

```powershell
verify-trajectories .verifier\trajectory.json --config .verifier\verifier.toml --out .verifier\verification_result.json
```

## Local Development

Run the verifier on the included sample:

```bash
python scripts/verify-trajectories.py data/sample_trajectories.json --out result.json
python scripts/evaluate-verifier.py data/sample_labeled_dataset.json
```

Run the repo smoke test prompt and fixture:

```bash
python scripts/verify-trajectories.py data/pi_speak_extension_smoke_trajectories.json --config data/pi_speak_node_verifier.toml --out result.pi-speak-smoke.json
```

The corresponding task-selection prompt lives at `prompts/smoke-testing.md`.

Run tests:

```bash
python -m unittest discover -s tests
```

The verifier accepts this input shape:

```json
{
  "task_id": "string",
  "task_description": "string",
  "candidates": [
    {
      "candidate_id": "string",
      "trajectory": [
        {
          "event_id": "string",
          "event_type": "assistant_message | tool_call | command | file_diff | artifact | error | final_answer",
          "timestamp": "string|null",
          "content": "string",
          "metadata": {
            "command": "string|null",
            "exit_code": "integer|null",
            "stdout": "string|null",
            "stderr": "string|null",
            "file_path": "string|null",
            "diff": "string|null"
          }
        }
      ]
    }
  ]
}
```

Output includes `ranked_candidates`, evidence-cited scoring claims,
`negative_evidence`, unsupported success claims, failure modes, missing
evidence, risk factors, uncertainty reasons, a deterministic semantic
certificate (`VERIFIED` / `INFERRED` / `UNKNOWN` style), and
`pairwise_comparisons` for candidates whose scores differ by less than `0.10`.

Deterministic checks currently cover passing and failing tests, missing tests,
nonzero exits, visible build/lint/syntax failures, stack traces, skipped or
truncated logs, suspicious success-looking command output, empty or
suspiciously small diffs, contradicted or unsupported success claims, retry
loops, no meaningful code change, and test-only changes when the task did not
ask for test changes.

Evaluation datasets are a list, or an object with `tasks`, where each task uses
the verifier input schema plus labels:

```json
{
  "tasks": [
    {
      "task_id": "example",
      "task_description": "Fix the bug.",
      "labels": {"candidate_a": 1, "candidate_b": 0},
      "candidates": []
    }
  ]
}
```

`evaluate-verifier.py` reports top-1 selection accuracy, pairwise ranking
accuracy, calibration error, a binned calibration table, false-positive rate,
false-negative rate, evidence-citation validity rate, and unsupported-success
detection rate.

The MVP is deliberately API-key-free. `scripts/trajectory_verifier.py` exposes a
`SemanticJudge` stub that can be replaced by an LLM-backed judge later; the
default implementation is deterministic and returns no adjustment.

## Benchmark Harness Setup

The older benchmark harness in this repo is separate from the deterministic
trajectory verifier above. It uses Gemini logprobs for Terminal-Bench and
SWE-bench experiments.

```bash
pip install google-genai tqdm
```

Create a `.env` file with your Vertex AI API key (required for logprob extraction):

```bash
echo "VERTEX_API_KEY=your_key_here" > .env
```

## Directory Structure

```
.
  README.md
  .env                          # API key (create this)
  scripts/
    verifier_core.py            # Gemini setup + scoring
    run_terminal_bench.py       # Terminal-Bench Evaluation
    run_swe_bench.py            # SWE-bench Verified Evaluation
  data/
    terminal_trajs/             # 5 trajectories x 89 tasks each for Terminal-Bench 2.0
    swebench_verified_trajs/    # 3 trajectories x 500 tasks each for SWE-bench Verified
  cache/                        # Cached API results (created on first run)
    cache_terminal_<agent>.json
    cache_swebench.json
  results/                      # Final result tables (written after each run)
    terminal_<agent>.txt
    swebench_verified.txt
```

## Trajectories

`data/terminal_trajs/forge_gpt54/` contains the Forge + GPT-5.4
submission downloaded from the
[Terminal-Bench 2 Leaderboard](https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard/tree/main/submissions/terminal-bench/2.0),
with 5 trajectories per task across 89 tasks:

| Scaffold | Base Model | Pass@1 |
|---|---|---|
| Forge | GPT-5.4 | 81.8% |

`data/swebench_verified_trajs/` contains 3 runs for SWE-bench
Verified (500 instances each) downloaded from the
[SWE-bench Leaderboard](https://github.com/swe-bench/experiments?tab=readme-ov-file#):

| Scaffold | Base Model | Pass@1 |
|---|---|---|
| mini-swe-agent | Claude-Opus-4.5 (high reasoning) | 76.8% |
| mini-swe-agent | Claude-Opus-4.6 | 75.6% |
| mini-swe-agent | Gemini-3-Flash (high reasoning) | 75.8% |

## Evaluating LLM-as-a-Verifier 

### Terminal-Bench

```bash
python scripts/run_terminal_bench.py --granularity 20 --n-verifications 4 --criteria 3
```

Expected:

| Method | Score | Rate |
|---|---|---|
| Pass@1 | 72.8/89 | 81.8% |
| LLM-as-a-Verifier | 76.9±0.3/89 | **86.4%** |
| Oracle (Bo5) | 80/89 | 89.9% |

### SWE-bench Verified

```bash
python scripts/run_swe_bench.py --granularity 20 --n-verifications 4 --criteria 3
```

Expected:

| Method | Score | Rate |
|---|---|---|
| Pass@1 | 380.3/500 | 76.1% |
| LLM-as-a-Verifier | 389.0±0.4/500 | **77.8%** |
| Oracle (Bo3) | 422/500 | 84.4% |

## How it works

Rather than reducing each distribution into a single discrete score (as in LLM-as-a-Judge), **LLM-as-a-Verifier** approximate the reward of
a trajectory $\tau$ on task $t$ as:

$$
R(t, \tau)
= \frac{1}{CK} \sum_{c=1}^{C} \sum_{k=1}^{K}
\sum_{g=1}^{G} p_{\theta}(v_g \mid t, c, \tau)\,\phi(v_g)
$$

**Where:**

- $C$ = number of evaluation criteria
- $K$ = number of repeated verifications
- $G$ = number of score tokens (granularity level)
- $p_{\theta}(v_g \mid t, c, \tau)$ = probability assigned by model $\theta$ to score token $v_g$
- $\phi(v_g)$ = maps each scoring token to a scalar value
- $V_{\text{score}} = \{v_1, \ldots, v_G\}$ = ordered set of discrete score tokens

To pick the best trajectory among $N$
candidates for a given task, we run a round-robin tournament. For every
pair $(i, j)$ the verifier produces $R(t, \tau_i)$ and $R(t, \tau_j)$
using the formula above. The trajectory with the higher reward
receives a win and the trajectory with the most
wins across all $\binom{N}{2}$ pairs is selected.
