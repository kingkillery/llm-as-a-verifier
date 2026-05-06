# Smoke-Testing Prompt

REPOSITORY / TASK SELECTION

Use the repository:

pi-speak-extension

First inspect the repo. Do not assume its structure.

Use this repo for the end-to-end verifier test if it has:
- runnable local tests, lint, build, or extension packaging commands
- meaningful code paths
- a realistic bug, feature, or reliability task
- enough observable execution evidence to evaluate trajectories

Do not use Spreadsheet_LLM_Encoder.
Do not reference spreadsheet LLM optimizer unless it appears in this repo.

TASK SELECTION CRITERIA

Choose a realistic task from pi-speak-extension.

The task should involve at least one of:
- browser extension behavior
- speech input / speech output handling
- permissions or manifest correctness
- content script behavior
- background/service worker behavior
- UI popup/options behavior
- accessibility behavior
- error handling for microphone, speech recognition, or unavailable APIs
- build/test/lint reliability
- regression coverage

Good task examples:
- Fix a speech recognition edge case and add tests.
- Improve extension permission handling and verify manifest behavior.
- Fix a content-script injection failure.
- Add graceful fallback when speech APIs are unavailable.
- Fix popup state handling after recognition errors.
- Add regression tests for command routing between popup, content script, and background script.

