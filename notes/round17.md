# Round 17 — codex-r16 수정 및 검증

## 변경 사항

- 두 평가 설정의 Claude `--settings` JSON에 하네스 소유 `claude_bash_gate.py`를 `PreToolUse`/`Bash` 훅으로 등록했다. 훅은 sandbox `work/` 밖에 있고, `onFailure: block` 및 `disableAllHooks: false`를 사용한다. 정확히 한 번의 `python3 -I .claude/skills/<skill>/scripts/clean.py <입력> <새 출력>`만 허용한다. 상대경로·실재 파일·새 출력·심볼릭 링크 부재·스크립트 SHA·python3 실행 파일을 확인한다. 합성 명령과 셸 메타문자는 거부한다. `--allowedTools`는 사전 승인 역할만 한다.

- 준비된 run 명령에서 `acceptEdits`를 뺐고, `claude_python_script` 평가에서 다시 지정하는 것도 거부한다. 이 모드는 파일 작업 외에 파일시스템 Bash까지 승인하여 평가 목적보다 권한이 넓다.

- `clean.py`는 기존 목적지(하드 링크 포함)를 거부하고 `open(..., 'x')`로 새 파일만 만든다. Eval 3은 공백이 있는 헤더 fixture를 정리하게 했고 원본 바이트 보존 검사도 추가했다.

- 게이트의 복합 명령, 치환, 리다이렉션, 래퍼, 별칭·심볼릭 링크, 절대경로, 추가 인자 거부와 하드 링크 목적지 거부를 테스트했다. `python3 -m pytest -q`: **516 passed**.

## 훅 문서 확인

[Claude Hooks reference](https://code.claude.com/docs/en/hooks)는 `hooks.PreToolUse[].matcher: "Bash"`, command handler의 `command`/`args`/`onFailure: "block" 및 `hookSpecificOutput`의 `hookEventName: "PreToolUse"`와 `permissionDecision: "allow"|"deny"` 형식을 명시한다. [CLI reference](https://code.claude.com/docs/en/cli-reference)는 `--disable-slash-commands`가 스킬·명령만 끄고, `--setting-sources`는 파일 기반 소스 선택이며, `--settings` 인라인 JSON이 파일 설정을 덮어쓴다고 명시한다. 두 arm의 생성 명령에 같은 훅이 들어감을 테스트했다. 모델 호출 금지에 따라 실제 Claude 세션에서의 훅 발화는 검증하지 않았다.

## `--live --dry-run` 실행 기록

### run

정확한 실행 명령:

```sh
python3 skill_eval.py run golden/skills/csv-cleaner --workspace golden/skills/csv-cleaner-workspace --engines claude --runs 1 --claude-model claude-sonnet-5-5 --claude-effort high --grader-engine codex --grader-effort high --tokens exec=135000,2000 --tokens trigger=190000,1500 --budget-usd 12.5 --live --dry-run
```

종료 코드: **3**

전체 출력(stdout + stderr):

```text
skill: csv-cleaner (/Users/pinesky/workspace/스킬_마스터/golden/skills/csv-cleaner)
workspace: /Users/pinesky/workspace/스킬_마스터/golden/skills/csv-cleaner-workspace
configurations: with_skill, without_skill | runs: 1 | engines: claude(claude-sonnet-5-5, effort high)
grader: codex(CLI default model, effort high) (yes/no rubric) + blind pairwise judge
eval set: 6 case(s), items static=12 llm=6 human=0, pressure cases=3; trigger queries: 20 (+10 / -10)
validation problems (0):
approval gates: requirements=approved, eval_set=NOT APPROVED
preflight problems (block --live):
isolation notes (not blocking):
  - claude: 33 synced and 150 plugin skill(s) in /Users/pinesky/.claude are not hidden before launch (Keychain login shares the config dir); if any loads, the post-run init check (skills exactly as expected, plugins == []) stops the iteration after that run
auth: subscription only (Claude: existing claude.ai Keychain login; Codex: ChatGPT login); `claude auth status --json` is checked only by a real --live (not by --dry-run)
grader budget: SOFT LIMIT (codex has no per-call cap): each grader/pairwise call starts only if its worst-case bound fits the remaining budget and is charged max(usage x price, bound); price ASSUMED $10.00/$50.00 per MTok (not verified, override with --price grader=IN,OUT)
grader isolation: the Codex grader runs `codex exec --sandbox read-only` in a fresh temp folder with an isolated CODEX_HOME holding a read-only copy of auth.json; that copy is READABLE by the grader agent (the read-only sandbox permits reads), and the grader prompt contains the untrusted transcript of the run under test, so a prompt injection could make it quote the token into grading.json / comparison-k.json evidence
sample commands (eval 1, run in <workspace>/iteration-N/<engine>/eval-1-evals-files-orders-2025-csv에-같은-주문이-여러-번/<config>/run-k/workdir):
  [claude with_skill] claude -p "evals/files/orders_2025.csv에 같은 주문이 여러 번 들어가 있어. 분석 전에 중복 행 …" --output-format stream-json --verbose --no-session-persistence --setting-sources project,local --strict-mcp-config --settings "{\"disableAllHooks\": false, \"disableBundledSkills\": true, \"enabledPlugins\": {\"cc-plugin-agents-md@builtin\": false, \"cc-plugin-claude-test@builtin\": false, \"cc-plugin-diff@builtin\": false, \"cc-plugin-mermaid@builtin\": false, \"cc-plugin-mods-guide@builtin\": false, \"cc-plugin-plugin-authoring@builtin\": false, \"cc-plugin-responsive-mode@builtin\": false, \"cc-plugin-sec-default@builtin\": false, \"cc-plugin-telemetry@builtin\": false, \"cc-plugin-tips@builtin\": false, \"cc-plugin-you-should-know@builtin\": false}, \"hooks\": {\"PreToolUse\": [{\"hooks\": [{\"args\": [\"/Users/pinesky/workspace/\\uc2a4\\ud0ac_\\ub9c8\\uc2a4\\ud130/claude_bash_gate.py\", \"csv-cleaner\", \"scripts/clean.py\", \"392ed75dcc5b41e2ab7b2558fb4103ac7974929045b8f05c979e406068abaf39\", \"/usr/bin/python3\"], \"command\": \"/Library/Developer/CommandLineTools/usr/bin/python3\", \"onFailure\": \"block\", \"type\": \"command\"}], \"matcher\": \"Bash\"}]}, \"skillOverrides\": {\"artifact-capabilities\": \"off\", \"artifact-components\": \"off\", \"artifact-data-table\": \"off\", \"artifact-design\": \"off\", \"artifact-diagramming\": \"off\", \"artifact-explainer\": \"off\", \"artifact-pr-review\": \"off\", \"artifact-report\": \"off\", \"batch\": \"off\", \"claude-api\": \"off\", \"claude-code-docs\": \"off\", \"claude-in-chrome\": \"off\", \"code-review\": \"off\", \"commit\": \"off\", \"cowork-plugin\": \"off\", \"dataviz\": \"off\", \"debug\": \"off\", \"design\": \"off\", \"design-sync\": \"off\", \"doc\": \"off\", \"doctor\": \"off\", \"explain-usage\": \"off\", \"fewer-permission-prompts\": \"off\", \"keybindings-help\": \"off\", \"loop\": \"off\", \"memory-types\": \"off\", \"pr\": \"off\", \"prototype\": \"off\", \"run\": \"off\", \"run-skill-generator\": \"off\", \"schedule\": \"off\", \"setup-claude\": \"off\", \"simplify\": \"off\", \"slides\": \"off\", \"update-config\": \"off\", \"verify\": \"off\", \"whiteboard\": \"off\", \"workflow-authoring\": \"off\", \"workshop\": \"off\"}}" --model claude-sonnet-5-5 --effort high --allowedTools "Bash(python3 -I .claude/skills/csv-cleaner/scripts/clean.py *)"
  [claude without_skill] claude -p "evals/files/orders_2025.csv에 같은 주문이 여러 번 들어가 있어. 분석 전에 중복 행 …" --output-format stream-json --verbose --no-session-persistence --setting-sources project,local --strict-mcp-config --settings "{\"disableAllHooks\": false, \"disableBundledSkills\": true, \"enabledPlugins\": {\"cc-plugin-agents-md@builtin\": false, \"cc-plugin-claude-test@builtin\": false, \"cc-plugin-diff@builtin\": false, \"cc-plugin-mermaid@builtin\": false, \"cc-plugin-mods-guide@builtin\": false, \"cc-plugin-plugin-authoring@builtin\": false, \"cc-plugin-responsive-mode@builtin\": false, \"cc-plugin-sec-default@builtin\": false, \"cc-plugin-telemetry@builtin\": false, \"cc-plugin-tips@builtin\": false, \"cc-plugin-you-should-know@builtin\": false}, \"hooks\": {\"PreToolUse\": [{\"hooks\": [{\"args\": [\"/Users/pinesky/workspace/\\uc2a4\\ud0ac_\\ub9c8\\uc2a4\\ud130/claude_bash_gate.py\", \"csv-cleaner\", \"scripts/clean.py\", \"392ed75dcc5b41e2ab7b2558fb4103ac7974929045b8f05c979e406068abaf39\", \"/usr/bin/python3\"], \"command\": \"/Library/Developer/CommandLineTools/usr/bin/python3\", \"onFailure\": \"block\", \"type\": \"command\"}], \"matcher\": \"Bash\"}]}, \"skillOverrides\": {\"artifact-capabilities\": \"off\", \"artifact-components\": \"off\", \"artifact-data-table\": \"off\", \"artifact-design\": \"off\", \"artifact-diagramming\": \"off\", \"artifact-explainer\": \"off\", \"artifact-pr-review\": \"off\", \"artifact-report\": \"off\", \"batch\": \"off\", \"claude-api\": \"off\", \"claude-code-docs\": \"off\", \"claude-in-chrome\": \"off\", \"code-review\": \"off\", \"commit\": \"off\", \"cowork-plugin\": \"off\", \"dataviz\": \"off\", \"debug\": \"off\", \"design\": \"off\", \"design-sync\": \"off\", \"doc\": \"off\", \"doctor\": \"off\", \"explain-usage\": \"off\", \"fewer-permission-prompts\": \"off\", \"keybindings-help\": \"off\", \"loop\": \"off\", \"memory-types\": \"off\", \"pr\": \"off\", \"prototype\": \"off\", \"run\": \"off\", \"run-skill-generator\": \"off\", \"schedule\": \"off\", \"setup-claude\": \"off\", \"simplify\": \"off\", \"slides\": \"off\", \"update-config\": \"off\", \"verify\": \"off\", \"whiteboard\": \"off\", \"workflow-authoring\": \"off\", \"workshop\": \"off\"}}" --model claude-sonnet-5-5 --effort high --allowedTools "Bash(python3 -I .claude/skills/csv-cleaner/scripts/clean.py *)" --disable-slash-commands
cost estimate:
engine  kind      model                     calls    tokens_in tokens_out        usd
claude  exec      claude-sonnet-5-5            12      1620000      24000       3.48
claude  trigger   claude-sonnet-5-5            20      3800000      30000       7.90
total calls 32, API-equivalent total $11.38 (subscription: not billed per token)
assumed tokens per call (in,out): exec=135000,2000, trigger=190000,1500, grader=12000,800, pairwise=16000,800, builder=80000,8000

LIVE DRY RUN: nothing was executed (no model call, no `claude auth status`, no files written).
budget: API-equivalent estimate $11.38 vs --budget-usd $12.50 (fits)
live gates that would refuse this command (1):
  - gate eval_set not approved (write /Users/pinesky/workspace/스킬_마스터/golden/skills/csv-cleaner-workspace/requirements.md, then `approve --gate eval_set`)
  (skipped in dry run: `claude auth status --json` (runs a CLI; only the real --live checks it))
claude --settings (executor launches; grader/builder use separate settings): {"disableAllHooks": false, "disableBundledSkills": true, "enabledPlugins": {"cc-plugin-agents-md@builtin": false, "cc-plugin-claude-test@builtin": false, "cc-plugin-diff@builtin": false, "cc-plugin-mermaid@builtin": false, "cc-plugin-mods-guide@builtin": false, "cc-plugin-plugin-authoring@builtin": false, "cc-plugin-responsive-mode@builtin": false, "cc-plugin-sec-default@builtin": false, "cc-plugin-telemetry@builtin": false, "cc-plugin-tips@builtin": false, "cc-plugin-you-should-know@builtin": false}, "hooks": {"PreToolUse": [{"hooks": [{"args": ["/Users/pinesky/workspace/\uc2a4\ud0ac_\ub9c8\uc2a4\ud130/claude_bash_gate.py", "csv-cleaner", "scripts/clean.py", "392ed75dcc5b41e2ab7b2558fb4103ac7974929045b8f05c979e406068abaf39", "/usr/bin/python3"], "command": "/Library/Developer/CommandLineTools/usr/bin/python3", "onFailure": "block", "type": "command"}], "matcher": "Bash"}]}, "skillOverrides": {"artifact-capabilities": "off", "artifact-components": "off", "artifact-data-table": "off", "artifact-design": "off", "artifact-diagramming": "off", "artifact-explainer": "off", "artifact-pr-review": "off", "artifact-report": "off", "batch": "off", "claude-api": "off", "claude-code-docs": "off", "claude-in-chrome": "off", "code-review": "off", "commit": "off", "cowork-plugin": "off", "dataviz": "off", "debug": "off", "design": "off", "design-sync": "off", "doc": "off", "doctor": "off", "explain-usage": "off", "fewer-permission-prompts": "off", "keybindings-help": "off", "loop": "off", "memory-types": "off", "pr": "off", "prototype": "off", "run": "off", "run-skill-generator": "off", "schedule": "off", "setup-claude": "off", "simplify": "off", "slides": "off", "update-config": "off", "verify": "off", "whiteboard": "off", "workflow-authoring": "off", "workshop": "off"}}
  built-in plugins disabled: 11 (binary /opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe: 11 cc-plugin-* names found)
  bundled skills set off: 39, never 'csv-cleaner' (binary scan: 34 registerBundledSkill names found); kill-switch survivors: design, doctor; child env CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 DISABLE_DOCTOR_COMMAND=1 ENABLE_CLAUDEAI_MCP_SERVERS=false
claude --allowedTools (with_skill and without_skill): Bash(python3 -I .claude/skills/csv-cleaner/scripts/clean.py *)
live command: python3 skill_eval.py run golden/skills/csv-cleaner --workspace golden/skills/csv-cleaner-workspace --engines claude --runs 1 --claude-model claude-sonnet-5-5 --claude-effort high --grader-engine codex --grader-effort high --tokens exec=135000,2000 --tokens trigger=190000,1500 --budget-usd 12.5 --live
```

### grade

정확한 실행 명령:

```sh
python3 skill_eval.py grade golden/skills/csv-cleaner --workspace golden/skills/csv-cleaner-workspace --iteration 3 --engines claude --runs 1 --claude-model claude-sonnet-5-5 --claude-effort high --grader-engine codex --grader-effort high --budget-usd 9.5 --live --dry-run
```

종료 코드: **2**

전체 출력(stdout + stderr):

```text
error: no iterations in /Users/pinesky/workspace/스킬_마스터/golden/skills/csv-cleaner-workspace
```

## 남은 단순 작업

1. 변경된 eval 3 fixture·검사와 스킬 SHA를 검토한 담당자가 `eval_set` 게이트를 다시 승인한다. 현재 run dry-run의 유일한 거부 사유다.

2. 모델 호출이 허용되는 시점에 위 run 명령에서 `--dry-run`을 빼고 iteration 3을 실행한다. 그 뒤 위 grade dry-run을 다시 실행해 게이트와 예산을 확인한다.

3. iteration 3 생성 후 grade와 report를 실행하고, 실제 결과에서 훅 발화와 기준군의 차단을 확인한다. Codex 채점 예산은 호출당 강제 한도가 없는 추정치다.

4. 별도 평가셋 작업으로 트리거 should-use 라벨과 fixture 부재 정책을 정한다.

## Round 18 — codex-r17 수정 및 검증

- P1: CSV 스크립트를 사용하는 Claude 실행의 **두 arm** 모두 `--permission-mode default`를 고정하고 `--allowedTools "Write,Edit,Bash(python3 -I .claude/skills/csv-cleaner/scripts/clean.py *)"`를 전달한다. `PreToolUse` Bash 게이트는 그대로 적용한다. 추가 인자로 permission mode를 덮어쓰려는 시도는 거부한다. `Write`와 `Edit`는 eval 4의 원본 파일에도 쓸 수 있지만, 이를 사전에 차단하지 않는다. eval 4의 기존 정적 검사가 도구 호출과 원본 바이트 변경을 측정한다.
- P2: 실행 전 검사에서 엔드포인트 관리 설정뿐 아니라 유효 Claude 설정 디렉터리의 `remote-settings.json`도 확인한다. `allowManagedHooksOnly` 또는 `disableAllHooks`가 `true`이면 Bash 게이트가 작동하지 않을 수 있으므로 실행을 거부한다. 두 제한값의 거부와 `false`일 때 허용을 테스트했다.
- 문서 확인: [Claude headless](https://code.claude.com/docs/en/headless)는 `-p`에서 `--allowedTools`의 도구 사전 승인과 시작 권한 모드 명시를 설명한다. [Claude permissions](https://code.claude.com/docs/en/permissions)는 `default` 모드와 차단 훅이 허용 규칙보다 우선함을 설명한다. 실제 훅 발화는 모델 호출 금지 때문에 확인하지 않았다.
- `python3 -m pytest -q`: **517 passed**.

준비된 run 명령을 다시 실행했다. 플래그 변경은 없어 정확한 명령은 아래와 같다.

```sh
python3 skill_eval.py run golden/skills/csv-cleaner --workspace golden/skills/csv-cleaner-workspace --engines claude --runs 1 --claude-model claude-sonnet-5-5 --claude-effort high --grader-engine codex --grader-effort high --tokens exec=135000,2000 --tokens trigger=190000,1500 --budget-usd 12.5 --live --dry-run
```

종료 코드 **3**. 검증 문제 **0**, preflight 문제 **0**, 예산 추정 **$11.38 / $12.50**. `with_skill`과 `without_skill` 샘플 명령 모두 `--permission-mode default --allowedTools "Write,Edit,Bash(python3 -I .claude/skills/csv-cleaner/scripts/clean.py *)"`를 출력했다. 실평가를 막는 유일한 게이트는 **eval_set 미승인**이다. `--dry-run`이므로 모델 호출과 파일 쓰기는 없었다.

남은 작업: 변경된 평가셋을 검토하고 `eval_set` 승인을 갱신한 뒤, 모델 호출이 허용되는 시점에 실평가를 실행한다.

## Round 19 — codex-r18 수정 및 검증

- P2: 게이트가 필요한 Claude 실행의 preflight는 훅 제한과 같은 관리 설정 출처들 및 유효 설정 디렉터리의 `remote-settings.json`에서 `allowManagedPermissionRulesOnly: true`를 확인한다. 발견하면 `--allowedTools` 승인 규칙이 버려질 수 있어 실행을 거부한다. 관리 출처와 서버 캐시 각각의 `true` 거부, `false` 허용, 게이트 없는 호출은 검사하지 않음을 `tests/test_round17.py`에 추가했다.
- P3: README 실행 규칙을 두 arm의 생성 인자와 일치시켰다: `--permission-mode default --allowedTools "Write,Edit,Bash(python3 -I .claude/skills/<name>/<script> *)"`.
- `python3 -m pytest -q`: **518 passed**.

준비된 run dry-run 명령:

```sh
python3 skill_eval.py run golden/skills/csv-cleaner --workspace golden/skills/csv-cleaner-workspace --engines claude --runs 1 --claude-model claude-sonnet-5-5 --claude-effort high --grader-engine codex --grader-effort high --tokens exec=135000,2000 --tokens trigger=190000,1500 --budget-usd 12.5 --live --dry-run
```

결과: 종료 코드 **3**. validation **0**, preflight **0**, 예상 API 환산 비용 **$11.38 / $12.50**. 두 arm 모두 위의 동일한 `--permission-mode default` 및 `--allowedTools`를 출력했다. 유일한 live gate는 기존과 같이 `eval_set` 미승인이다. `--dry-run`으로 모델 호출·파일 쓰기는 없었다.
