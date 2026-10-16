# 에바 (Eva) — SKILL.md 스킬 평가기

스킬_마스터는 **SKILL.md 스킬**을 만들고(build), 들여다보고(inspect), 평가하고(evaluate), 고치는(improve) 도구입니다. 스킬을 [agentskills.io 명세](https://agentskills.io/specification)와 Anthropic 스킬 작성 지침으로 검사하고, 실패 이유와 고칠 방법을 빌더 에이전트에게 돌려줍니다. Python 3.9 표준 라이브러리만 씁니다.

> **범위 안내 — 에이전트 평가는 분리 예정**: `AGENT.md` 에이전트 검사, `scenarios/`, 실행 기록(transcript) 채점, `no-answer-lookup` 검사는 앞으로 별도 프로젝트 **"agent master"**로 옮깁니다. 옮기기 전까지는 코드와 테스트를 지우지 않고 그대로 둡니다(`eva.py`의 agents / transcript grading 섹션, `golden/agents`, `bad/agents`, `transcripts/`). 새 기능은 스킬 쪽에만 추가합니다.

## 원칙: 정석으로 (우회하지 않고)
"우회하지 않고 정석으로"는 부정행위를 막자는 말이 아닙니다. **당장 쉬운 길, 지금만 통하는 가장 단순한 길을 고르지 말라**는 뜻입니다. 유행 중이거나 확실하지 않은 주제는 먼저 현재 업계에서 실제로 어떻게 하는지 조사하고, 그 표준 방식을 따릅니다. 조사한 출처는 아래처럼 남깁니다.

이 저장소에서 지키는 방식:
1. **명세가 있으면 명세가 기준입니다.** 스킬 형식은 우리가 정하지 않습니다. 필드, 길이, 이름 규칙, 디렉터리 구조는 agentskills.io 명세와 그 참조 검증기(`skills-ref`), Anthropic `skill-creator`의 `quick_validate.py`에 맞춥니다. 명세보다 엄격한 자체 규칙은 아래 "자체 규칙"에 따로 표시합니다.
2. **평가는 eval 중심 개발(eval-driven development)로 합니다.** 스킬은 평가셋(`evals/evals.json`)을 먼저 갖춰야 합니다. 그다음 스킬 있음과 없음(또는 이전 버전)을 비교하고, 결과를 보고 고치는 일을 반복합니다. Anthropic, agentskills.io, OpenAI가 공통으로 권하는 방식입니다.
3. **검사를 느슨하게 해서 테스트를 통과시키지 않습니다.** 테스트가 실패하면 코드나 픽스처를 표준에 맞게 고칩니다. 명세에 맞추느라 검사를 더 엄격하게 하는 것은 괜찮습니다.
4. **모르는 것은 추정하지 않습니다.** 구현하지 않은 업계 기능은 "아직 없음"으로 적습니다(아래 목록).

조사한 출처(이번에 직접 읽은 문서):
- agentskills.io 명세: https://agentskills.io/specification
- agentskills.io 스킬 출력 평가: https://agentskills.io/skill-creation/evaluating-skills.md
- agentskills.io description 최적화(트리거 평가): https://agentskills.io/skill-creation/optimizing-descriptions.md
- agentskills.io 문서 색인: https://agentskills.io/llms.txt
- 참조 검증기 skills-ref: https://raw.githubusercontent.com/agentskills/agentskills/main/skills-ref/src/skills_ref/validator.py
- skills-ref 파서: https://raw.githubusercontent.com/agentskills/agentskills/main/skills-ref/src/skills_ref/parser.py (`strictyaml`로 읽음, metadata 값은 `str()`로 바꿈)
- strictyaml 파서(스칼라 타입 처리, flow/anchor/tag 금지): https://raw.githubusercontent.com/crdoconnor/strictyaml/master/strictyaml/parser.py
- CommonMark ATX 제목 규칙: https://spec.commonmark.org/0.31.2/#atx-headings
- Anthropic skill-creator: https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/SKILL.md
- skill-creator 스키마(evals.json, grading.json): https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/references/schemas.md
- skill-creator 검증기: https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/scripts/quick_validate.py
- Anthropic 스킬 작성 모범 사례: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Anthropic 테스트 설계(eval 모범 사례): https://platform.claude.com/docs/en/test-and-evaluate/develop-tests
- OpenAI eval 모범 사례: https://developers.openai.com/api/docs/guides/evaluation-best-practices (platform.openai.com 주소에서 리다이렉트됨)
- (평가 하네스) skill-creator 로컬 사본 `~/.claude/plugins/cache/claude-plugins-official/skill-creator/*/skills/skill-creator/`: `SKILL.md`, `references/schemas.md`(evals·grading·timing·benchmark·comparison JSON), `scripts/aggregate_benchmark.py`(통계·delta), `scripts/run_eval.py`(트리거 감지·trigger_rate), `scripts/run_loop.py`(60/40 분할, 최대 5회), `agents/comparator.md`(블라인드 비교). 캐시된 7개 사본의 `schemas.md`는 모두 같은 내용(md5 동일)
- (평가 하네스) Claude Code 비대화형 실행: https://code.claude.com/docs/en/headless, CLI 플래그: https://code.claude.com/docs/en/cli-reference (`-p`, `--output-format stream-json`, `--json-schema`, `--tools ""`, `--disable-slash-commands`, `--max-budget-usd`, `--no-session-persistence`)
- (평가 하네스) Codex 비대화형 실행: https://learn.chatgpt.com/docs/non-interactive-mode (`codex exec --json`, 이벤트 `item.completed`·`turn.completed`의 `usage`), Codex 스킬 위치: https://learn.chatgpt.com/docs/build-skills (`.agents/skills`)
- (평가 하네스) Claude 요금: https://platform.claude.com/docs/en/about-claude/pricing (2026-10-15 확인)

## 사용법
```
python3 eva.py check golden/skills/pdf-summarizer        # 하나 검사 (종료코드: PASS 0, FAIL 1, REVIEW 3)
python3 eva.py check <path> --json                      # JSON 출력
python3 eva.py check-all golden                         # 폴더 전체 (하나라도 FAIL이거나 검사 대상이 0개면 1, FAIL 없이 REVIEW가 있으면 3)
python3 eva.py feedback <path> --round 1                # FEEDBACK.md / FEEDBACK.json 작성
python3 eva.py index golden                             # INDEX.md / registry.json 생성
python3 loop.py <path> --builder "<cmd>"                # 자동 피드백 루프 (LOOP.md, 빌더 실패 시 종료코드 2, REVIEW면 3)
python3 eva.py check <agent> --transcript run.json      # [분리 예정] 에이전트 실행 기록 채점
python3 eva.py eval plan <skill>                        # 스킬 평가 하네스: 계획·예상 비용만 출력 (dry run, 아무것도 실행 안 함)
python3 eva.py eval run <skill> --live --budget-usd 20   # 실제 실행 (승인·예산 조건, 아래 "스킬 평가 하네스")
python3 eva.py eval run <skill> --live --dry-run --budget-usd 20  # 그 실제 명령의 모든 게이트만 확인 (아무것도 실행 안 함)
python3 -m unittest discover -s tests -v                # 테스트 (pytest도 pytest.ini의 testpaths로 같은 tests/만 수집)
```

## 스킬 구조 (agentskills.io 명세)
```
<name>/
├── SKILL.md            # 필수: frontmatter + 본문
├── scripts/            # 선택: 실행 코드
├── references/         # 선택: 필요할 때 읽는 문서
├── assets/             # 선택: 템플릿, 데이터
└── evals/evals.json    # 에바 필수(H, 명세에는 없음): skill-creator 형식 평가셋
```
- frontmatter 필드는 `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`만 쓸 수 있습니다. 예외(호스트별 허용, H): Claude Code 스킬 문서(https://code.claude.com/docs/en/skills)의 frontmatter 표에 있는 `argument-hint`(자동완성 인자 힌트, 비어 있지 않은 YAML 문자열 — 따옴표 없는 `true`/`no`/`on`·`null`/`~`·`42`/`3.5`처럼 YAML이 문자열 아닌 값으로 읽는 값은 `field-types` 실패, `"true"`처럼 따옴표로 감싸면 통과)와 `disable-model-invocation`(따옴표 없는 `true`/`false`)은 받습니다. 명세 밖 필드라 skills-ref·quick_validate는 이 둘도 거부하므로, 다른 호스트로 옮길 스킬에는 쓰지 마세요. 그 밖의 필드(예: 예전의 `inputs:`/`outputs:`)는 `frontmatter-fields` [필수] 실패입니다. 입출력은 본문 `## Inputs`/`## Outputs`에 씁니다.
- frontmatter는 YAML이어야 합니다. 첫 줄과 닫는 줄은 정확히 `---`입니다(H: skills-ref는 `---`로 시작하고 다음 `---`까지를 읽음). 그 사이는 `key: value` 매핑이고, 키는 중복될 수 없습니다. 들여쓰기에는 공백만 씁니다(탭 금지). 값에 `: `가 들어가거나 값이 YAML 기호(`&`, `*`, `!`, `{`, `@` 등)로 시작하면 YAML 오류이므로 따옴표로 감쌉니다. 공백 뒤 `#`과 값 맨 앞의 `#`은 YAML 주석이라 값이 거기서 끝납니다(오류 아님, YAML과 동일). 그래서 `description: # TODO`는 빈 값(null)이고 `description-present` 실패입니다. 따옴표 값은 YAML처럼 읽습니다: `"..."` 안의 `\"`, `\\`, `\n`, `\uXXXX` 등 이스케이프를 풀고(모르는 이스케이프는 오류), `'...'` 안의 `''`는 `'` 하나입니다. `\U`·`\u`·`\x` 값이 U+10FFFF를 넘으면 오류로 보고합니다(PyYAML은 이 입력에서 YAMLError가 아닌 예외로 멈춤). 닫는 따옴표 뒤에 주석이 아닌 글자가 있거나 따옴표가 같은 줄에서 닫히지 않으면 오류입니다(여러 줄 따옴표 값은 지원하지 않음). `>`·`|` 블록 스칼라(`>-`·`|-` 포함)는 PyYAML처럼 읽습니다: 블록 안의 들여쓴 `#` 줄은 주석이 아니라 내용이고, `>`는 줄을 공백으로 접고 빈 줄은 줄바꿈으로 남기며, 끝 줄바꿈은 skill-creator `quick_validate`가 읽는 범위(`---\n`과 `\n---` 사이)와 같게 처리합니다. 테스트에서 파서 결과를 PyYAML `safe_load`와 비교합니다. 이 비교 테스트는 PyYAML이 없으면 실패합니다(건너뛰려면 `EVA_ALLOW_NO_PYYAML=1`을 명시적으로 설정, 이때 경고를 출력).
- metadata 값은 따옴표를 강제하지 않습니다. skills-ref는 strictyaml로 읽어 `version: 1.0`, `beta: yes` 같은 값도 문자열로 받습니다. 명세 예시처럼 `"1.0"`으로 감싸는 것을 권장하지만 검사하지는 않습니다.
- `evals/evals.json` (skill-creator / agentskills.io 형식):
  ```json
  {"skill_name": "<frontmatter name>",
   "evals": [{"id": 1, "prompt": "실제 사용자 요청", "expected_output": "성공의 모습",
              "files": ["evals/files/x.csv"], "assertions": ["확인 가능한 문장"]}]}
  ```
  `files`, `assertions`, `layer`는 선택입니다. `layer`는 그 케이스를 채점하는 방식이며 값은 정확히 소문자 `static`·`llm`·`human` 중 하나여야 합니다(그 밖의 값·대문자는 `evals-valid` 실패, H). 세 layer가 모두 있을 필요는 없습니다. agentskills.io 문서는 `assertions`, skill-creator `schemas.md`는 `expectations`라고 써서 두 이름을 모두 받습니다. assertion 항목은 문자열 외에 `{"text": str, "layer": "static|llm|human", "check": {...}}` 객체도 받습니다(H, 항목별 layer — 아래 "스킬 평가 하네스"). `check` 내용은 `eva.py eval validate`가 검사합니다.

## 스킬 평가 기준
[필수]는 점수와 상관없이 하나라도 실패하면 FAIL입니다. 출처 표시: **S**=agentskills.io 명세, **V**=skills-ref / quick_validate 검증기, **B**=Anthropic 모범 사례, **C**=skill-creator / agentskills.io 평가 문서, **H**=자체 규칙(명세에 없거나 명세보다 엄격함. 다른 출처와 함께 적힌 H는 그 출처보다 엄격한 부분).

| id | 내용 | 출처 |
|---|---|---|
| `frontmatter` [필수] | `---`로 감싼 YAML 매핑 | S |
| `frontmatter-fields` [필수] | 명세의 6개 필드만 허용. 호스트별 허용(Claude Code 문서 필드): `argument-hint`, `disable-model-invocation` — 명세·skills-ref에는 없음, 값 타입은 `field-types`가 검사 | S, V, H |
| `field-types` [필수] | name·description·compatibility·license·allowed-tools는 문자열, metadata는 문자열→문자열 매핑(목록·중첩 매핑 불가) (S). name·description·compatibility 값이 PyYAML에서 숫자·불리언·null·날짜로 읽히면(예: `name: 123`) 따옴표 필요 (V: quick_validate가 `yaml.safe_load` 후 `str`인지 검사). license·allowed-tools가 비어 있으면 실패 (H). metadata 값은 따옴표 강제 안 함 | S, V, H |
| `name-format` [필수] | skills-ref `_validate_name`과 같음: NFKC 정규화 후 1~64자, 소문자(`name == name.lower()`), 유니코드 글자·숫자(`str.isalnum()`)와 `-`만, `-`로 시작·끝나지 않음, `--` 없음. 참고: skill-creator quick_validate는 ASCII `^[a-z0-9-]+$`만 허용하므로 `한국어-교정` 같은 이름은 quick_validate에서는 실패합니다 | S, V |
| `name-matches-folder` [필수] | NFKC(폴더 이름) = NFKC(name). 폴더 이름은 `realpath`로 풉니다: `eva.py check .`, `../x/`, 끝 슬래시, 심볼릭 링크(링크가 가리키는 실제 폴더 이름) | S, V |
| `name-reserved` [필수] | name에 `anthropic`, `claude`가 들어가면 안 됨 | B |
| `description-present` [필수] | description이 비어 있지 않은 문자열. `description:`처럼 값이 없으면(null) 문자열 `'None'`으로 치지 않고 실패 | S |
| `description-no-xml` [필수] | description에 `<`, `>` 없음 | B, V |
| `description-length` [필수] | 1~1024자. 1024자를 넘으면 FAIL | S, B |
| `description-min-length` | 20자 이상 (명세는 비어 있지만 않으면 됨) | H |
| `description-when` [필수] | 언제 쓰는지를 **영어 사용 조건 문장**으로 적음(따옴표·백틱 안 글은 보지 않음). 인정: ① `Use`/`Used`/`Invoke`/`Trigger`로 **시작하는 문장**(선택적 `this skill`·`this`·`it`·`-ly` 부사·`for/to <작업>`) + `when`/`whenever`, ② 사용 동사(`use`/`using`/`invoke`/`trigger`/`read`) 바로 뒤의 `this skill` + `when`("… use this skill when …"), 또는 문장 첫머리 `This skill` + 조동사·be + 사용 동사(`should be used`·`can be used`·`must be invoked`·`is used`) + `when`, ③ "Read this skill before …"/"Read this skill when …". 그 뒤에 구체적인 조건(영어 단어 2개 이상, `needed`·`asked`·`appropriate` 같은 막연한 단어만은 불가). 마침표·`!`·`?` 바로 뒤에 대문자가 오면 공백이 없어도 조건은 거기서 끝남("Use when needed.The user …"는 실패). 실패: 다른 말 뒤의 `when`("Counts words when files change"), 수동형·분사형("files loaded when", "hooks called when", "Applies to X when", "…, used when", "This skill is loaded when", "This skill applies when", "Config files for this skill are loaded when"), 다른 동사("This skill counts words when", "Skip/Avoid this skill when"), 부정형("Do not use when", "Don't use when", "Never use when", "This skill should not be used when", "Cannot use this skill when", "Avoid using this skill when"; `not`·`never`·`n't`·`cannot`·`avoid`·`skip`·`without`), "Use this.", 한국어 조건문("…할 때 사용한다", "…하면 쓴다")만 있는 경우. 명세는 "Should describe … when to use it"(권장)이라 [필수]로 강제하는 것과 영어 문장만 인정하는 것은 자체 규칙 | S, B, H |
| `description-pov` [필수] | 1·2인칭 표현 금지 (B: "Always write in third person", "I can help you…"·"You can use this…" 피하기). 영어 `I`, `me`, `my`, `mine`, `we`, `our`, `us`, `you`, `your`, `yourself`(대소문자 무관: `YOU`, `WE`도 실패)와 한국어 `저는`, `제가`, `저희`, `나는`, `내가`, `우리는/우리가/우리의`, `당신`, `너는`, `네가`, `여러분`을 찾음. 따옴표·백틱 안의 인용(`"fix my code"`, `'…'`, `“…”`, `` `my-app` ``)은 사용자 발화로 보고 제외. 하이픈·밑줄로 붙은 식별자(`my-app`), `I/O`, 로마 숫자(`Phase I`, `Type I`), 대문자 `US`(국가·약어)는 대명사로 보지 않음. 명령형("Use when …")은 허용. 주어를 생략한 한국어 1·2인칭은 찾지 못함 | B |
| `single-purpose` | 여러 일을 나열하지 않음(점수 항목, [필수] 아님). 휴리스틱: description에서 **나열 항목을 여는 영어 작업 동사**의 종류를 셉니다. 항목은 글 첫머리, `.` `;` `:` `!` `?` `,` `(` `)` `&` 뒤, 접속어 `and`·`or`·`then`·`also`·`plus`·`to`·`as well as` 뒤에서 시작하므로 `;`를 `,`나 `and`로 바꿔도 같은 수가 나옵니다. 동사는 고정 어휘 목록(`TASK_VERBS`)의 원형·`-s`/`-es`/`-ies`·`-ing` 형태이고, 명사로 자주 쓰이는 낱말(`report`, `schedule`, `format` 등 `AMBIGUOUS_TASK_VERBS`)은 `-ing` 형태이거나 `to` 바로 뒤일 때만 셉니다. 서로 다른 작업 동사가 3개 이상(`SINGLE_PURPOSE_MAX_VERBS`=2 초과)이거나 `and also`·`as well as`·`plus`·`그리고 또`·`뿐만 아니라`가 있으면 실패. 한계: 따옴표 안 글, 사용 조건 문장(`description-when`이 인정하는 "Use when …" 문장 — 요청 예시를 나열하는 곳이라 제외), 쓰지 않을 때를 적은 문장("Do NOT use for …", "not for …")에만 나열한 작업, 목록에 없는 동사, 한국어 동사 나열("검사하고, …내며, …만들고")은 세지 않습니다. 같은 일의 동의어("reading, parsing, or extracting")도 각각 셉니다 | H |
| `compatibility-length` [있으면 필수] | 1~500자 | S, V |
| `inputs-declared`, `outputs-declared`, `example-present` | 본문 `## Inputs`, `## Outputs`, `## Example` 제목 (명세는 예시 섹션을 권장). 제목은 CommonMark ATX 규칙: 앞 공백 0~3칸, `#` 1~6개 뒤 공백·탭(또는 줄 끝). 코드 블록 안 제목은 무시 | S(권장), H |
| `size-limit` | SKILL.md 500줄 미만, 20KB 이하 (20KB는 "본문 5000토큰 미만" 권장치를 대신하는 자체 기준) | S, B, H |
| `refs-relative` | 참조는 스킬 폴더 기준 상대경로, 슬래시(`/`) 사용, 폴더 밖(`..`)이나 절대경로 금지 | S, B |
| `refs-exist` [필수] | 참조한 파일이 실제로 있음. 위치와 상관없이 없는 파일을 하나라도 참조하면 **필수 실패**(판정 FAIL)입니다. 판정 순서: ① 먼저 탈출 경로를 가립니다 — 절대경로(`/etc/hosts`, `/Users/…`, `~/…`), 드라이브 경로(`C:/…`, `C:\…`, 드라이브 상대 `C:relative.txt` — 한 글자 스킴은 URL이 아니라 경로로 봄), 역슬래시 경로, 그리고 `..` 정규화나 심볼릭 링크(가리키는 대상이 없는 끊긴 링크 포함)를 풀었을 때 스킬 폴더와 리포 루트 밖으로 나가는 참조는 대상 파일이 있든 없든 존재 검사 전에 **필수 실패**입니다(예: `../../../../definitely-no-such-file`). ② 탈출하지 않은 참조 중 스킬 폴더 안의 있는 파일(번들)은 통과. ③ 스킬 폴더 밖이지만 리포 안의 있는 파일(`..`·심볼릭 링크로 가리킴), 그리고 스킬 폴더에는 없지만 스킬을 감싼 git 리포 루트 기준으로는 있는 파일(예: pinesky-skills의 `scripts/check_skills.py`)은 실패도 통과도 아닌 **needs-review**("outside the skill folder, inside the git repo, not bundled" / "repo-root-relative reference, not bundled")로 보고해 판정이 REVIEW가 됩니다. ④ 그 밖에 어디에도 없는 참조(번들 경로 포함)는 필수 실패. 리포 루트는 스킬 폴더 realpath에서 위로 올라가며 `.git`(폴더 또는 워크트리 파일)이 있는 첫 폴더이고, 리포 루트 기준 해석에서도 `..`·절대경로·역슬래시 참조와 심볼릭 링크를 풀었을 때 리포 루트 밖으로 나가는 경로는 인정하지 않습니다. 리포가 없으면 스킬 폴더 밖은 모두 탈출입니다. 명세는 참조를 상대경로로 쓰라고만 하고 파일 존재 검사는 정하지 않으므로 자체 규칙. 참조 찾기: ① 코드 블록 밖 마크다운 링크의 상대경로, ② 코드 블록 밖 인라인 코드·`**굵게**`·일반 문장 속 `scripts/…`, `references/…`, `assets/…` 경로(예: "Run scripts/x.py", "`scripts/clean.py`를 실행" — 경로 끝에 붙은 한국어 조사와 `**`는 떼어 냄), ③ 코드 블록(```` ``` ````, `~~~`) 안에서는 실행 명령만, 한 줄을 `&&`, `||`, `;`, `|`로 나눈 **모든 명령**에서: 인터프리터(`python`, `python3`, `bash`, `sh`, `zsh`, `node`, `deno run`, `ruby`, `perl`, `Rscript`, `php`, `pwsh`, `uv run`, 앞에 `VAR=값` 허용) 뒤의 번들 경로, 또는 명령 맨 앞 `./scripts/…`·`scripts/…` 직접 실행. 단 `./` 없는 `scripts/…`는 들여쓴 줄, `# 설명`만 붙은 줄(`scripts/extract.py   # helper`), 트리 문자(`├ └ │`)가 있는 블록에서는 파일 목록으로 보고 참조가 아님. `#` 뒤 셸 주석은 보지 않음. URL, `#앵커`, 폴더 언급(`scripts/`), 글로브(`assets/*.png`, `assets/icon*` — 끝의 `*`는 앞에 같은 수의 `*`로 연 강조일 때만 떼어 냄), `my-scripts/x`처럼 다른 단어에 붙은 경로, 코드 블록 안의 실행 명령이 아닌 줄은 참조가 아님. `__밑줄 강조__`로 감싼 경로는 아직 인식하지 않음 | H |
| `refs-one-level` | SKILL.md가 참조한 .md 파일이 SKILL.md가 직접 참조하지 않는 다른 파일을 다시 참조하지 않음(참조 사슬 금지). 명세 원문 "Keep file references one level deep from SKILL.md. Avoid deeply nested reference chains."와 B 원문 "All reference files should link directly from SKILL.md"를 옮긴 것이라 명세보다 엄격하지 않음. 권장 문구("Keep", "Avoid")라 [필수]가 아닌 점수 항목. SKILL.md 자신, 같은 파일, `#앵커`, URL로 가는 링크는 사슬이 아님 | S, B |
| `refs-path-depth` | 참조 경로는 폴더 한 단계까지(`references/x.md`). 명세에는 없는 자체 규칙 | H |
| `reference-toc` [해당 시] | SKILL.md가 링크한 100줄 넘는 .md 파일은 제목 바로 다음에 목차(`## Contents`/`## 목차`) | B |
| `version-format` [있으면 필수] | `metadata.version`은 SemVer `x.y.z`. 자체 규칙이며, 명세 예시의 `"1.0"`은 여기서 실패함 | H |
| `evals-valid` [필수] | `evals/evals.json`이 있고 위 형식을 따름: `skill_name` = name, `id` 정수 중복 없음, `prompt`·`expected_output` 문자열, `files`는 스킬 안에 실제로 있음, `assertions`/`expectations`는 문자열 목록. 형식은 C, 파일이 반드시 있어야 하는 것은 자체 규칙(명세의 디렉터리 구조에 `evals/`는 없음) | C, H |
| `evals-count` [필수] | 평가 케이스 3개 이상. 개수 근거는 B ("At least three evaluations created"), C ("Start with 2-3 test cases"), [필수]로 강제하는 것은 자체 규칙 | B, C, H |

**판정**: 아래를 모두 만족하면 PASS입니다.
1. 통과율(N/A 제외) 80% 이상 (`PASS_THRESHOLD`)
2. [필수] 항목 전부 통과
3. 선택 항목(`compatibility-length`, `version-format`, `reference-toc`)은 대상이 없으면 N/A(점수 분모에서 뺌)이고, 있으면 통과해야 합니다.
4. 카테고리 하한: N/A가 아닌 검사가 있는 카테고리(single-task, io, structure, evaluation 등)마다 1개 이상 통과하고 통과율이 `CATEGORY_FLOOR`(50%) 이상
5. 형식이 틀린 입력은 예외로 죽지 않고 FAIL 보고서로 나옵니다.

## 업계 표준 대비 아직 없는 스킬 기능
`eva.py check`는 정적 검사만 하고 모델을 실행하지 않습니다. 스킬 있음/없음 벤치마크, 채점·집계, 트리거 평가, assertion 품질 분석, 블라인드 비교, 사람 리뷰 큐, 반복 루프는 이제 `skill_eval.py`(아래 "스킬 평가 하네스")에 있습니다. 다만 실제 모델 실행으로 검증한 적은 아직 없습니다(테스트는 모두 가짜 엔진). 아래는 여전히 없는 것입니다.
- **description 자동 최적화** (C): train 60% / validation 40% 분할, LLM이 description 수정안을 내고 validation 점수로 최선을 고르는 루프. 하네스는 트리거 비율만 재고, description을 고치지 않습니다.
- **리뷰 뷰어** (C): skill-creator `eval-viewer/generate_review.py` 같은 HTML 뷰어. 하네스는 `comparison.md`와 `human_review.json`만 만듭니다.
- **SKILL.md 수정안 생성** (C): 실패한 assertion·사람 피드백·실행 기록을 LLM에 주고 수정안을 받는 일. `eval loop --builder-prompt`는 사용자가 쓴 지시문에 직전 `comparison.md`를 붙여 Claude에 한 번 넘길 뿐, 실패 assertion별 수정안 프롬프트를 따로 만들지 않습니다. `loop.py`는 정적 검사 피드백만 돌립니다.
- **토큰 예산** (S): 메타데이터 약 100토큰, 본문 5000토큰 미만 권장. 토크나이저가 없어 바이트(20KB)로 대신합니다.
- **모델별 테스트** (B): Haiku / Sonnet / Opus 각각으로 확인하기
- **본문 작성 품질** (B): 시점에 따라 틀려질 정보 없음, 용어 일관성, 스크립트 오류 처리·상수 근거, MCP 도구 전체 이름(`Server:tool`) — 자동 검사 없음
- **description 시점**: 동사 형태(3인칭 "Processes…" / 명령형 "Use this skill when…")는 강제하지 않고 1·2인칭 대명사만 `description-pov`로 막습니다. 따옴표 안 인용은 검사하지 않으므로, 인용 안에 숨긴 1·2인칭은 찾지 못합니다. 주어를 생략한 한국어 1·2인칭(예: "도와드릴게요")은 찾지 못합니다.

## 스킬 평가 하네스 (skill_eval.py)
스킬을 실제 에이전트(Claude Code, Codex)로 실행해 **스킬 있음 vs 없음**(또는 새 버전 vs 이전 버전)을 비교합니다. `python3 skill_eval.py <명령> <skill>` 또는 `python3 eva.py eval <명령> <skill>`.

**안전 장치(기본은 dry run)**: `--live`와 `--budget-usd`가 둘 다 없으면 어떤 CLI도 실행하지 않고 계획과 예상 비용만 출력합니다(작업 폴더도 만들지 않음). 실제 CLI를 띄우는 곳은 `live_exec` 하나이고, `main()`이 아래 조건을 모두 확인한 뒤에만 켭니다. `run`·`loop`·`grade`·`calibrate`의 `--live`에 같은 조건이 걸립니다: ⓪ 구독 인증만 사용(API 키 환경변수 없음, `claude auth status`가 claude.ai 로그인, Codex `auth_mode`가 ChatGPT — 아래 **Subscription auth**), ① 유한한 `--budget-usd > 0`(**API 환산 비용** 기준, 아래 **Cost**), ② 평가셋 검증 문제 0개, ③ `requirements`·`eval_set` 승인, ④ 모든 모델의 가격을 앎(`--price`는 유한한 양수만, `--tokens`도 양수만), ⑤ (run·loop) preflight 문제 0개, ⑥ (run·loop) **`--strict-budget`이 기본값**이라 Codex처럼 강제 가능한 호출당 상한이 없는 엔진은 `--live` 실행 자체를 거부(Round 6). 위험을 알고 `--no-strict-budget`을 준 경우에만 Codex를 실행하며, 그때는 Codex의 호출당 최악 비용 상한이 예산 안이어야 함(**Codex 예산은 soft limit** — 아래 호출당 예산 제어), ⑦ 예상 비용 ≤ 예산. **`loop`는 매 iteration 시작 전에 ①~⑦을 다시 확인합니다**(평가셋을 다시 읽어 검증, `eval_set` 승인 해시 — 입력 파일 포함 — 재확인, preflight 재실행, 남은 iteration 비용 ≤ 남은 예산). 하나라도 실패하면 `gates-failed`로 멈춥니다.
- **builder**: 셸 명령 `--builder`는 없앴습니다(지정하면 dry-run이든 `--live`든 오류). 임의 셸 명령은 유료 CLI를 예산 밖에서 부를 수 있기 때문입니다. 대신 `--builder-prompt FILE`은 iteration 사이에 `claude -p` 한 번을 `live_exec`로 실행합니다: 작업 폴더 = 스킬 폴더, `--tools Read,Edit,Write,Glob,Grep`(Bash 없음 → 다른 CLI를 띄울 수 없음), `--permission-mode acceptEdits`, `--disable-slash-commands`, `--max-budget-usd`(남은 예산의 절반), 비용은 같은 예산에서 차감(보고가 없으면 상한액 차감). 직전 iteration의 `comparison.md`를 프롬프트에 넣습니다. `--builder-model`(기본 `claude-opus-5-5`)과 `--tokens builder=IN,OUT`(가정 80k/8k)로 `loop` 예상 비용에 (iteration 수 − 1)회가 더해집니다. builder가 `evals/`를 고치면 다음 iteration 전 승인 재확인에서 멈춥니다. 편집 범위가 스킬 폴더로 한정되는지는 확인하지 않았습니다(추정: acceptEdits는 작업 폴더 기준).
- **예상 비용의 근거**: `--live`일 때는 항상 검증된 평가셋 개수로 계산합니다. `--cases/--llm-cases/--trigger-queries`는 `estimate`·`plan` 전용이고 다른 명령에 주면 오류입니다. `grade --live`는 그 iteration 폴더에 실제로 있는 run 수(LLM 항목 있는 run + pairwise 짝)로, `calibrate --live`는 골든 예시 개수 × 채점 호출로, `loop --live`는 한 iteration 비용(+`--golden` 보정) × `--max-iterations`로 계산합니다.
- **호출당 예산 제어(H)**: 실행 중 호출마다 API 환산 비용(토큰×가격이 먼저, CLI 보고값 `total_cost_usd`는 그보다 클 때만, 둘 다 없거나 보고값이 0이고 사용량도 없으면 그 호출의 상한 — 아래 **Cost**)을 예산에서 빼고, 예산이 모자라면 멈추고 `plan.json`의 `stopped`에 적습니다. Claude(실행·채점 모두)에는 남은 예산의 절반(`CLAUDE_CAP_HEADROOM` 0.5, 센트 내림)만 `--max-budget-usd`로 넘깁니다. CLI가 이 상한을 얼마나 넘을 수 있는지는 문서에서 확인하지 못해 여유를 둔 값입니다. 그 값이 $0.01 미만이면 호출하지 않습니다. **Codex는 호출당 상한 옵션이 없습니다.** 그래서 호출 전마다 최악 비용 상한(가정 토큰 × `WORST_CASE_FACTOR` 3 × 단가)이 남은 예산 안일 때만 호출합니다. 이 값은 추정 상한이지 **강제 상한이 아니며**, 실제 한 호출이 이보다 비쌀 수 있습니다(`--tokens`로 가정을 키우면 더 보수적). 그래서 **Codex 예산은 soft limit**으로 표시합니다: `estimate`·`plan` 출력의 `budget: SOFT LIMIT: codex …` 줄, `run`·`loop --live` 시작 때 stderr 경고 `WARNING: budget SOFT LIMIT: codex …`, `plan.json`의 `budget_enforcement`(`claude: per_call_cap`, `codex: soft_limit`), 예산 중단 문구 "(codex budget is a soft limit: no per-call cap)". **`--strict-budget`은 기본값입니다**(Round 6, 이전에는 opt-in). 그래서 `run`·`loop --live`는 soft limit 엔진(현재 Codex)이 `--engines`에 있으면(기본 `--engines claude,codex` 포함) 아무것도 실행하지 않고 거부합니다. **`--no-strict-budget`은 위험한 opt-out**입니다: 한 Codex 호출이 `--budget-usd`를 넘을 수 있고, Codex에 넣는 `auth.json` 사본을 시험 중인 에이전트가 읽을 수 있습니다(아래 **Subscription auth**). 이 플래그로 실행하면 stderr에 두 위험을 모두 경고합니다. Claude 실행·채점·builder는 `--max-budget-usd`가 있어 영향이 없습니다(그 상한을 CLI가 얼마나 넘을 수 있는지는 위처럼 미확인).
- `--claude-arg`·`--codex-arg`로 모델·예산을 바꾸거나 격리를 여는 플래그는 거부합니다: Claude `--model`, `--fallback-model`, `--max-budget-usd`, `--settings`, `--setting-sources`, `--plugin-dir`, `--plugin-url`, `--add-dir`, `--mcp-config`; Codex `-m`, `--model`, `-p`, `--profile`, `--oss`, `-c/--config model…`, `-C`/`--cd`, `--add-dir`. 가격은 `--claude-model`/`--codex-model` 기준이기 때문입니다.

| 명령 | 하는 일 |
|---|---|
| `validate` | evals.json(항목 layer·check, pressure)과 trigger_evals.json(정확히 10+10) 검사 |
| `estimate` | 예상 호출 수·토큰·비용 표. `--cases/--llm-cases/--trigger-queries`로 가상 개수, `--tokens KIND=IN,OUT`, `--price KEY=IN,OUT` |
| `plan` | 검증 문제, 승인 상태, preflight 문제, 엔진·설정별 예시 명령, 예상 비용 (항상 dry run) |
| `run --live --dry-run --budget-usd X` | (`loop`·`grade`·`calibrate`도 같음) 그 실제 명령의 모든 live 게이트를 나열하고 실제 명령을 출력. 아무것도 실행·기록하지 않음(Round 6) |
| `run --live --budget-usd X` | `iteration-N/` 하나 실행: 엔진 × 케이스 × 설정 × `--runs`(기본 3) + 트리거 쿼리 × `--runs` |
| `grade` | 정적 검사 먼저 → (`--live`일 때) LLM yes/no 채점 + 블라인드 pairwise → human 항목은 `human_review.json` 큐. `--live` 없으면 정적 검사와 큐만 |
| `calibrate --golden F --live` | 사람이 라벨 붙인 골든 예시로 LLM 채점자 일치율·Cohen's kappa 계산 → `calibration.json` |
| `report` | `benchmark.json`, 엔진별 `benchmark.json`, `comparison.md`, `gate.json` (문제 있으면 종료코드 3) |
| `approve --gate G --by NAME` | 승인 기록(`approvals.json`, 파일 sha256에 묶임 — 파일이 바뀌면 승인 무효) |
| `register` | `registration.json` 작성. `benchmark.json`에 기록된 시험 당시 스킬 폴더 해시·평가셋 해시가 지금과 다르면 거부(다시 실행 필요). `gate.json`이 `benchmark.json`의 gate와 다르면 거부. 마지막 iteration에 문제가 있으면 `registration` 승인 필요 |
| `snapshot` | 현재 스킬을 `skill-snapshot/`에 복사 (`--baseline old`의 기준) |
| `loop --live --budget-usd X [--builder-prompt FILE]` | run→grade→(calibrate)→report 반복. 매 iteration 전 모든 live 게이트 재확인. 문제 없으면 멈춤, 있으면 계량되는 Claude builder 실행 후 다음 iteration. 최대 5회 |

**평가셋 형식**
- `evals/evals.json`: skill-creator 형식 + 항목별 layer. 문자열 assertion은 케이스 `layer`(없으면 `llm`)를 따릅니다. `static` 항목은 기계 검사 `check`가 필수입니다: `regex`/`not_regex`(`pattern`, `target`: `final`|`transcript`, `flags`: `"i"`), `tool_call`(`tool`, `input_regex`, `min` 기본 1, `max`), `skill_triggered`(`expected`), `file_exists`(실행 workdir 기준 상대경로). 예: `{"text": "Skill 호출 0회", "layer": "static", "check": {"type": "tool_call", "tool": "Skill", "min": 0, "max": 0}}`.
- `SKILL.md` frontmatter `name`도 설치 폴더(`work/.claude/skills/<name>`)와 기본 workspace 이름(`<name>-workspace`)이 되므로 같은 규칙(`^[a-z0-9][a-z0-9._-]{0,63}$`, `..` 금지)을 지켜야 합니다. 어기면 `validate` 문제(`SKILL.md name: …`)이고, 기본 workspace 계산·스킬 설치도 거부합니다. 설치 직전에는 대상 경로가 sandbox의 스킬 폴더 안인지 다시 확인합니다.
- 케이스 `name`(선택)은 폴더 이름이 되므로 경로 한 칸만 허용합니다: `^[a-z0-9][a-z0-9._-]{0,63}$`, `..` 금지(예: `x/../../target`은 검증 실패, `eva check`의 `evals-valid`에서도 실패). `name`이 없으면 프롬프트 slug(영숫자·한글·`-`만)를 씁니다. 그와 별도로 workspace 아래에 쓰거나 지우기 전에 실제 경로(심볼릭 링크·`..` 해석 후)가 그 iteration 폴더 안인지 확인하고, 밖이면 멈춥니다.
- 압박 시나리오(pressure): 케이스에 `"pressure": {"kinds": ["time", "authority"], "context": "..."}`. `context`를 프롬프트 앞에 붙여 실행하고, 보고서에 pressure/normal 통과율을 따로 냅니다.
- `evals/trigger_evals.json`: skill-creator 형식 `[{"query": str, "should_trigger": bool}]`, should-trigger 10개 + should-not 10개(H). 스킬이 있는 설정에서만 실행하고, 쿼리별 trigger_rate ≥ 0.5면 트리거로 봅니다(run_eval.py 기본값).
- 골든(채점자 보정): `{"examples": [{"id", "prompt", "expected_output"?, "output", "item", "label": bool}]}`.

**작업 폴더** (기본: 스킬 폴더 옆 `<name>-workspace/`, skill-creator 관례. `--workspace`로 변경)
```
<name>-workspace/
├── requirements.md, approvals.json, registration.json, skill-snapshot/
└── iteration-N/
    ├── plan.json, benchmark.json, comparison.md, gate.json, human_review.json, calibration.json
    ├── cost.json                      # 채점·보정·builder 호출의 API 환산 비용 기록 (run 비용은 각 timing.json)
    └── <engine>/                      # claude | codex
        ├── benchmark.json             # 이 엔진만, skill-creator 스키마 그대로
        ├── eval-<id>-<name>/
        │   ├── eval_metadata.json, comparison-<k>.json
        │   └── with_skill|without_skill|old_skill/run-<k>/
        │       ├── workdir/           # 격리 실행 폴더에서 돌려받은 결과물 (설치한 스킬 사본은 제외). 실행 자체는 임시 sandbox에서
        │       ├── outputs/transcript.json, outputs/final.md
        │       ├── timing.json        # total_tokens, duration_ms, input/output/cached tokens, cost_usd(CLI 보고값), api_equivalent_usd/_source, billing
        │       └── grading.json
        └── trigger/<config>/results.json   # run_eval.py 출력 형식
```

**엔진 어댑터** (`Engine.command/parse/triggered`, 테스트에서는 `exec_fn`을 가짜로 바꿈)
- Claude Code: `claude -p <prompt> --output-format stream-json --verbose --no-session-persistence --setting-sources project,local --strict-mcp-config --settings <JSON> --model M`. 스킬 있음 = sandbox `work/.claude/skills/<name>/`에 복사, 스킬 없음 = 추가로 `--disable-slash-commands`(로컬 `claude --help`: "Disable all skills"). 트리거 = `Skill` 도구 호출의 `skill`이 이름과 같음(agentskills.io 예시 jq와 같은 기준) 또는 `.../skills/<name>/SKILL.md` Read(run_eval.py도 Read를 봄). 권한 모드는 기본으로 넣지 않습니다. `acceptEdits`를 추가로 지정할 수 있지만, CSV 준비 명령에는 쓰지 않습니다. 임의 `--allowedTools` 추가 인자는 거부하고, 스킬 전용 Python 스크립트 권한과 Bash 게이트는 아래 `claude_python_script` 평가 설정에서 생성합니다.
- Codex: `codex exec --json --skip-git-repo-check --ephemeral --sandbox workspace-write <prompt>`. 스킬 있음 = sandbox `work/.agents/skills/<name>/`. 환경: `HOME`·`CODEX_HOME`을 sandbox 안으로 바꿈(아래 Eval isolation). 따라서 사용자 `config.toml`(모델 등)도 읽지 않고 CLI 기본값을 씁니다. 토큰은 `turn.completed.usage`(`reasoning_output_tokens`는 비용 상한 쪽으로 출력에 더함 — 과금 규칙은 확인 안 함). `-m`은 `--codex-model`을 줄 때만 붙입니다(공식 페이지에서 확인 못 함). **트리거 감지는 (추정)**: Codex 문서에 스킬 호출 이벤트가 없어, 항목에 `skills/<name>/SKILL.md`가 나오면 트리거로 봅니다. 첫 실제 실행에서 확인 필요.
- preflight와 실행 격리: 아래 **Eval isolation**.

### Eval isolation
목표: 실행마다 깨끗한 환경. with_skill에는 **시험 중인 스킬 하나만**, without_skill에는 **스킬 없음**. 사용자 수준 스킬(`~/.claude/skills`, claude.ai synced, 플러그인, `~/.agents/skills`, `~/.codex/skills`)이 섞이지 않게 하고, **사용자의 기존 로그인은 바꾸지 않습니다.** 확인할 수 없으면 실행하지 않습니다(fail closed).

**공통 (run마다)**
- `--run-root`(기본: 시스템 임시 폴더) 아래에 새 `skill-eval-<engine>-XXXX/`를 만들고 `work/`에 그 케이스의 입력 파일과(with_skill이면) 스킬 사본(`evals/` 제외)만 넣습니다. 실행이 끝나면 `work/`의 결과물(설치한 스킬 사본 제외)을 `run-k/workdir/`로 옮겨 정적 검사에 쓰고 sandbox를 지웁니다(예외가 나도 지움). 삭제는 오류를 무시하지 않습니다: 실패하면 sandbox 아래 폴더를 소유자 쓰기 가능으로 바꿔 최대 3회(`RMTREE_ATTEMPTS`) 다시 시도하고, 그래도 폴더가 남아 있으면 "could not be removed … may hold the read-only Codex auth.json copy" 문제로 iteration을 멈춥니다(`plan.json` `stopped`; 실행 중 예외와 겹치면 두 내용을 함께 보고). 이 경우 직접 지워야 합니다. 실행 폴더가 더는 workspace(스킬 옆) 아래가 아니므로 저장소 안 `.claude/skills`가 상위 폴더로 보이는 문제도 없어졌습니다.
- 실행 직전 검사(sandbox): 프로젝트 스킬 폴더가 정확히 `[<name>]`(with) 또는 `[]`(without)인지, sandbox의 모든 상위 폴더에 `.claude/skills`·`.agents/skills` 스킬이 없는지. 문제가 있으면 CLI를 띄우기 전에 iteration을 멈춥니다(`plan.json` `stopped`).

**Claude Code** — HOME·`CLAUDE_CONFIG_DIR`은 그대로 둡니다.
- 근거(확인): "If you've set the `CLAUDE_CONFIG_DIR` environment variable, Claude Code … keys the macOS Keychain entry to that directory too, so a session with a different `CLAUDE_CONFIG_DIR` reads a different entry." (https://code.claude.com/docs/en/authentication, 2026-10-15 읽음). 임시 HOME/CONFIG_DIR은 로그인을 잃으므로 쓰지 않습니다. `--bare`도 "OAuth and keychain are never read"(로컬 `claude --help`)라 쓰지 않습니다.
- `--setting-sources project,local`: `"user"` 소스가 "`~/.claude/skills/`, `~/.claude/commands/`, and `~/.claude/agents/`"와 사용자 settings.json을 읽는다고 문서에 있습니다(https://code.claude.com/docs/en/agent-sdk/claude-code-features — SDK 옵션 설명. **CLI 플래그가 똑같이 동작하는지는 추정**). 플러그인 활성화(`enabledPlugins`)도 사용자 settings.json에 있으므로 플러그인 스킬도 빠질 것으로 봅니다(추정). claude.ai synced 스킬: skills 페이지가 `user`를 뺀 `--setting-sources`면 동기화하지 않는다고 하지만, 이미 내려받은 `~/.claude/skills/synced/…` 사본을 안 읽는지는 확인 못 함(추정) — 그래서 아래 init 검사로 확인합니다.
- `--strict-mcp-config`("Only use MCP servers from --mcp-config", 로컬 help), 환경변수 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `ENABLE_CLAUDEAI_MCP_SERVERS=false`(SDK 문서 "What settingSources does not control" 표), `--settings '{"disableBundledSkills": true}'`("To turn bundled skills off, use the `disableBundledSkills` setting", https://code.claude.com/docs/en/skills) — 두 설정이 같은 조건이 되도록.
- **내장(builtin) 플러그인 끄기 (Round 14)**: 첫 실제 실행이 init 검사에서 멈췄습니다 — `disableBundledSkills: true`였는데도 `cc-plugin-agents-md@builtin`·`cc-plugin-telemetry@builtin` 등 내장 플러그인과 스킬 `design`·`doctor`·`plugin-authoring`이 로드됨(`iteration-1/plan.json` `stopped`, 기록된 command). 내장 플러그인 id는 `<name>@builtin`이고 `settings.enabledPlugins["<name>@builtin"]`로 켜고 끕니다(https://code.claude.com/docs/en/plugins/mods/admin). 그래서 모든 Claude 실행(executor·trigger·grader·builder)의 `--settings`에 `enabledPlugins: {"<id>@builtin": false, …}`를 넣습니다. 목록 = 2.1.300 바이너리의 `cc-plugin-*` 이름 11개(정적 목록) ∪ 실행 시 설치된 `claude` 바이너리를 다시 훑어 찾은 이름(프로세스당 한 번, 캐시). 스캔이 실패해도 정적 목록은 꺼집니다. `cc-plugin-sec-default`가 false를 따르는지, `design`·`doctor`가 어디서 오는지·끄는 공식 방법은 **확인 못 함** — 그래서 init 검사(플러그인 `[]`, 스킬 정확히 일치)는 그대로 두고, 무엇이든 남아 로드되면 그 run에서 멈춥니다(fail closed).
- **번들 스킬 끄기 (Round 15)**: `disableBundledSkills`는 번들 스킬을 모두 끄지만 `/doctor`는 남깁니다(v2.1.205+). `/doctor`는 환경변수 `DISABLE_DOCTOR_COMMAND`나 `skillOverrides {"doctor": "off"}`로 숨깁니다. `skillOverrides` 값은 `on`/`name-only`/`user-invocable-only`/`off`이고, `off`는 Claude와 `/` 메뉴 모두에서 숨기며 번들 스킬에도 적용됩니다(플러그인 스킬은 `enabledPlugins`로) — https://code.claude.com/docs/en/skills, https://code.claude.com/docs/en/slash-commands "Override skill visibility from settings". 2.1.300 바이너리(`claude.exe`, 문자열만 읽음)에서 확인: `registerBundledSkill`(축약 이름 `Zs`)에 `survivesBundledKillSwitch:!0`를 붙여 등록한 스킬은 `doctor`(`isEnabled:()=>!…DISABLE_DOCTOR_COMMAND`)와 `design`(`Ppt="design"`) 두 개뿐이고, kill switch가 켜지면 `getBundledSkills`가 `bundledSkillKillSwitchSurvivors`만 돌려줍니다 — 첫 실제 실행에서 남은 `design`·`doctor`가 이것입니다. 그래서 모든 Claude 실행의 `--settings`에 `skillOverrides`로 번들 스킬 이름 전부를 `"off"`로 넣습니다: 정적 목록 39개(2.1.300의 `registerBundledSkill` 호출 이름. 템플릿으로 등록되는 `artifact-report`·`artifact-data-table`·`artifact-explainer`·`doc`·`slides`는 바이너리를 직접 읽어 넣음) ∪ 실행 시 설치된 바이너리 스캔(리터럴 이름과, 같은 chunk 또는 파일 전체에서 값이 하나뿐인 식별자만 해석; 프로세스당 한 번, 약 6초, 캐시). **시험 스킬 이름은 절대 넣지 않습니다**(executor·trigger는 run의 스킬 이름, builder는 `spec["skill_name"]`; grader는 시험 스킬이 없어 전부 off). 시험 스킬 이름이 번들 스킬 이름과 같으면 끌 수도 없고 init 검사로 구별할 수도 없으므로 실행 전에 거부합니다(preflight). 자식 환경에 `DISABLE_DOCTOR_COMMAND=1`도 넣습니다. init 검사는 그대로입니다. 이 설정이 실제로 `design`·`doctor`를 빼는지는 모델 호출(`claude -p`의 system/init) 없이는 **확인 불가**합니다.
- 무료 확인 명령 `python3 skill_eval.py check-builtin-plugins`: `claude --setting-sources project,local --settings <실행 settings> plugin list --json`(모델 호출 없음)을 돌려 `@builtin` 항목의 `enabled`를 봅니다. 2.1.300에서는 `plugin list --json`이 설치한 플러그인만 보여 주고 `plugin details`(옵션 `--json` 없음)는 enabled 상태를 보여 주지 않아 **확인 불가(종료코드 1)**로 끝납니다(2026-10-15 실행). 실제로 무엇이 로드됐는지는 `claude -p`의 system/init 이벤트(모델 호출)로만 알 수 있습니다.
- without_skill: `--disable-slash-commands`(로컬 help "Disable all skills").
- 실행 후 검사(fail closed): stream-json `system/init` 이벤트의 `skills`·`plugins` 목록으로 — `plugins`가 **명시적인 빈 목록**(필드가 없거나 목록이 아니면 확인 불가로 실패), 로드된 스킬 집합이 **정확히** `{시험 스킬}`(with_skill) 또는 `{}`(without_skill). 알 수 없는 이름이든 사용자 수준 스킬이든 하나라도 더 있으면 누출입니다(사용자 수준 이름이면 오류에 따로 표시). `plugin:<name>`처럼 이름공간이 붙은 같은 이름도 시험 사본으로 치지 않습니다. init 이벤트나 `skills` 목록이 없으면 확인 불가로 보고 실패 처리. 이 필드 이름은 Agent SDK 문서 기준 **(추정)**이며 첫 실제 실행에서 확인해야 합니다. 위반하면 그 run을 오류로 기록하고 iteration을 멈춥니다(이미 쓴 비용은 차감).
- preflight: 시험 스킬과 **같은 이름의 사용자 수준 사본**(`~/.claude/skills/**`, synced 포함, 플러그인)이 있으면 거부합니다(`--setting-sources`로 가려지는지 미확인이므로). 사용자 settings.json의 인증 설정도 봅니다(값은 읽거나 출력하지 않고 키 존재만 확인): `apiKeyHelper`나 `env`의 API 키 변수는 API 키 인증이라 거부합니다(Round 7부터 `env`의 `CLAUDE_CODE_OAUTH_TOKEN`도 거부: 인증은 Keychain 로그인만 씀). 현재 이 PC의 settings.json에는 그런 키가 없습니다(`enabledPlugins`만 있음, 2026-10-15 확인).
- **모든 settings 소스의 API 키 인증 거부**: API 키 환경변수를 지워도 settings 파일의 `apiKeyHelper`나 `env`의 API 키 항목이 다시 넣을 수 있습니다. 그래서 run이 읽을 수 있는 모든 settings 파일에서 이 키 이름을 찾고(값은 읽어 출력하지 않음), 있으면 거부합니다. 파일은 있는데 JSON으로 읽히지 않으면 확인 불가로 거부합니다(fail closed).
  - preflight: 사용자 `<config dir>/settings.json`·`settings.local.json`(config dir = `CLAUDE_CONFIG_DIR`, 없으면 `~/.claude`), `--run-root`와 그 모든 상위 폴더의 `.claude/settings.json`·`.claude/settings.local.json`, managed 소스 전부와 server-managed 캐시(아래 **Round 5** 참고).
  - run마다(실행 직전 sandbox 검사): sandbox `work/.claude/settings.json`·`settings.local.json`(입력 파일 `evals[].files`로 들어올 수 있음), sandbox의 모든 상위 폴더, managed settings. `--setting-sources project,local`이 cwd 위 상위 폴더의 settings도 읽는지는 미확인(추정)이라 상위 폴더도 모두 봅니다.

**Codex** — run마다 `HOME=<sandbox>/home`, `CODEX_HOME=<sandbox>/codex_home`.
- 스킬 위치(https://learn.chatgpt.com/docs/build-skills, 조사 에이전트 인용): REPO `$CWD/.agents/skills`·상위·`$REPO_ROOT/.agents/skills`, USER `$HOME/.agents/skills`, ADMIN `/etc/codex/skills`, SYSTEM(내장). `$CODEX_HOME/skills`(이 PC의 `~/.codex/skills`에 사용자 스킬 11개 + `.system/`)는 공식 문서에 없고 제3자 자료만 있음(추정). 둘 다 sandbox로 바꾸므로 어느 쪽이든 가려집니다. HOME을 바꾸면 `~/.agents/skills`가 가려진다는 것은 문서 표현("$HOME/.agents/skills") 기준 **추정**(코드 미확인).
- 인증: "`file` stores credentials in `auth.json` under `CODEX_HOME`"(https://learn.chatgpt.com/docs/auth). ChatGPT 로그인의 auth.json을 sandbox `codex_home/`에 **읽기 전용 사본**으로 둡니다. API 키(`CODEX_API_KEY`/`OPENAI_API_KEY`)는 쓰지 않고 거부합니다. 자세한 내용은 아래 **Subscription auth**.
- sandbox에 `config.toml`이 없으므로 사용자 Codex 설정(모델, MCP 등)은 쓰지 않습니다. 모델은 `--codex-model`(없으면 CLI 기본값).
- 실행 직전 검사: sandbox HOME의 `.agents/skills`, sandbox CODEX_HOME의 `skills`(`.system/` 내장 스킬 제외), `/etc/codex/skills`에 스킬이 하나라도 있으면 거부. Codex 채점자(`--grader-engine codex`)도 실행 직전에 같은 검사(빈 작업 폴더, sandbox 상위 폴더의 `.agents/skills`, sandbox HOME/CODEX_HOME, `/etc/codex/skills`)를 거쳐 걸리면 호출 없이(비용 0) 거부합니다(Round 11). `run`/`loop --live`는 Codex 시험 대상 + Codex 채점자 조합을 `--no-strict-budget`이어도 호출 전에 거부합니다(`grade`가 그 run을 채점하지 않으므로). Codex는 로드된 스킬 목록을 보고하는 이벤트가 문서에 없어 실행 후 검사는 auth 링크만 합니다(추정 한계).
- 확인 못 한 것: 이 세션에서 `codex exec --help` 실행이 허용되지 않아 Codex 플래그는 문서로만 확인했습니다. `.system` 스킬이 새 CODEX_HOME에 자동 설치되는지(제3자 자료는 그렇다고 함).

**preflight**(`run`·`loop --live`를 막음, 읽기 전용, `plan`에도 표시): API 키 환경변수가 설정됨, run-root와 그 모든 상위 폴더의 `.claude/skills`·`.agents/skills`에 스킬이 있음, Claude 같은 이름 사용자 사본, `user-invocable: false`인 claude.ai synced 스킬(Round 6), 모든 settings 소스(사용자·run-root 상위 폴더의 project/local·managed)의 API 키 인증과 `env`의 `CLAUDE_CODE_OAUTH_TOKEN`(Round 7), `/etc/codex/skills`의 스킬, Codex file 방식 auth.json이 없거나 `auth_mode`가 ChatGPT가 아님. `claude auth status`는 CLI를 실행해야 하므로 preflight가 아니라 `--live` 게이트에서만 확인합니다. 사용자 수준 Codex 스킬(`~/.agents/skills`, `~/.codex/skills`)은 sandbox HOME/CODEX_HOME으로 가려지므로 더는 거부 사유가 아닙니다. 끊어진 심볼릭 링크와 링크 순환은 건너뜁니다(테스트로 확인).

#### Round 5 보강 (Codex r4 리뷰)
이번에 직접 읽은 문서(2026-10-15): https://code.claude.com/docs/en/env-vars, https://code.claude.com/docs/en/managed-settings, https://code.claude.com/docs/en/server-managed-settings, https://code.claude.com/docs/en/agent-sdk/skills, https://code.claude.com/docs/en/agent-sdk/typescript(`SDKSystemMessage`, `ApiKeySource`).
- **Claude 호출 경로 하나**: 실행·채점(grader)·builder 모두 같은 격리 인자(`claude_isolation_args()`: `--setting-sources project,local --strict-mcp-config --settings {"disableBundledSkills":true, "enabledPlugins":{<모든 @builtin id>:false}, "skillOverrides":{<시험 스킬 외 모든 번들 스킬>:"off"}}` — enabledPlugins는 Round 14, skillOverrides와 자식 환경 `DISABLE_DOCTOR_COMMAND=1`은 Round 15)와 같은 자식 환경(`claude_child_env()`)을 씁니다. grader·builder는 `launch_claude()`를 거치며, 격리 인자가 없거나 cwd·managed·server 캐시 settings에 API 키/제공자 설정이 있으면 **호출 전에** 거부합니다(과금 없음). grader는 호출마다 새 빈 임시 폴더를 cwd로 쓰고(이전: 하네스를 띄운 폴더) 끝나면 지웁니다. grader에는 `--disable-slash-commands`도 붙습니다.
- **제공자 라우팅 거부 + 제거**: env-vars 문서의 제공자 전환·엔드포인트·자격 선택 변수 — `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY`, `CLAUDE_CODE_USE_MANTLE`, `CLAUDE_CODE_USE_ANTHROPIC_AWS`, `CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST`, `ANTHROPIC_BASE_URL`과 각 제공자 `*_BASE_URL`, `ANTHROPIC_VERTEX_PROJECT_ID`, `ANTHROPIC_FOUNDRY_RESOURCE`, `ANTHROPIC_PROFILE`, `ANTHROPIC_CONFIG_DIR`, `ANTHROPIC_FEDERATION_RULE_ID`(`PROVIDER_ENV`) — 와 추가 자격 변수 `ANTHROPIC_AWS_API_KEY`, `ANTHROPIC_FOUNDRY_API_KEY`, `ANTHROPIC_FOUNDRY_AUTH_TOKEN`, `AWS_BEARER_TOKEN_BEDROCK`, `ANTHROPIC_IDENTITY_TOKEN`을 API 키처럼 거부하고 모든 자식 환경에서 지웁니다. settings 파일의 `env`에 있어도 거부합니다. Codex 쪽 base URL 변수는 1차 문서로 확인하지 못해 넣지 않았습니다(미확인).
- **실제로 적용되는 settings 소스**: config dir은 `CLAUDE_CONFIG_DIR`("Override the configuration directory (default: `~/.claude`)", env-vars). managed는 managed-settings 문서대로 `managed-settings.json` + 같은 폴더의 `managed-settings.d/*.json`(숨김 파일·`.json` 아닌 파일 제외, Claude Code와 같은 규칙; macOS `/Library/Application Support/ClaudeCode/`, Linux·WSL `/etc/claude-code/`), macOS 구성 프로파일 도메인 `com.anthropic.claudecode`, server-managed 캐시 `<config dir>/remote-settings.json`(server-managed-settings 문서의 `~/.claude/remote-settings.json`). 구성 프로파일의 파일 위치 `/Library/Managed Preferences/[<user>/]com.anthropic.claudecode.plist`는 macOS 표준 위치이고 Claude 문서에는 없습니다 **(추정)** — 있으면 `plistlib`으로 읽고, 못 읽으면 거부합니다. `managed-settings.d`를 나열할 수 없거나 파일이 JSON이 아니면 거부(fail closed). Windows·WSL의 HKLM/HKCU 레지스트리는 이 하네스가 읽을 수 없으므로 Claude 실행을 거부합니다.
- **남는 한계**: 캐시가 없는 Team/Enterprise 계정은 실행 시점에 server-managed 설정을 새로 받아 올 수 있고, 비대화형(`claude -p`) 실행에는 승인 없이 그 run에만 적용됩니다(server-managed-settings 문서). 이것은 실행 전에 볼 수 없으므로 아래 `apiKeySource` 실행 후 검사로 막습니다(server-managed settings는 Team/Enterprise 플랜에만 있음, 같은 문서).
- **심볼릭 링크 차단**: 스킬 폴더(`evals/` 등 복사 제외 항목 빼고)에 스킬 밖을 가리키거나 끊어진 symlink가 있으면 설치(`copytree`) 전에 거부합니다(`escaping_links`, old_skill 기준선도 같음). 입력 파일(`evals[].files`)도 symlink를 따라간 실제 경로가 스킬 폴더 안이어야 복사합니다. 스킬 안을 가리키는 링크는 허용.
- **인증 사본 먼저 삭제**: sandbox 정리 순서는 ① `after_run`(사본 변경 비교) → ② Codex `auth.json` 사본만 따로 삭제하고 없어졌는지 확인(실패 시 폴더 권한을 고쳐 재시도, 그래도 남으면 쓰기 가능으로 바꿔 내용을 비움 — "content was erased" / 못 비우면 "IT MAY STILL HOLD A TOKEN"으로 보고) → ③ 나머지 sandbox 삭제. sandbox 생성 중 실패해도 같은 순서.
- **실행 뒤 실패한 호출도 차감**: 실행 엔진·grader·builder 호출이 시작된 뒤 예외(타임아웃 `TimeoutExpired` 등)가 나면 상한(Claude `--max-budget-usd` cap, Codex는 worst-case bound, 예산이 무한이면 bound)을 API 환산 비용으로 차감하고, run은 `timing.json`(`cap_charged`)·transcript 오류("call failed after launch (<예외 이름>)")로 기록한 뒤 iteration을 멈춥니다. grader·builder는 `cost.json`에 기록하고 `EvalError`로 멈춥니다(loop는 `builder-failed`). `LiveDisabled`(아직 시작 안 함)는 차감하지 않습니다. `loop`는 run이 멈춘 iteration(`plan.json` `stopped`)을 채점하거나 builder로 넘기지 않고 `run-stopped`로 끝납니다. 예외 메시지 원문은 기록하지 않고 예외 이름만 남깁니다.
- **스킬 소스를 실행 전에 강제**: `system/init`의 `skills` 배열은 "user-invocable skills only"이고 `user-invocable: false` 스킬은 "loads and remains available to Claude, but doesn't appear in the array"입니다(agent-sdk/skills). 그래서 init 검사는 보조 검사로만 남기고, 실행 전에 소스를 막습니다.
  - (Round 6에서 바뀜) Round 5의 `CLAUDE_CODE_OAUTH_TOKEN` 방식(run마다 빈 per-run config dir)과 "synced/플러그인 스킬이 하나라도 있으면 거부"는 없앴습니다. 지금 규칙은 아래 **Round 6**.
  - Keychain 로그인(config dir 공유): 일반 `<config dir>/skills/<name>`은 문서상 `user` 소스("include `'user'` to keep your personal skills")라 `--setting-sources project,local`로 가려집니다. claude.ai synced 스킬(`skills/synced/…`)과 플러그인 스킬은 그렇게 가려진다는 문서가 없습니다(추정 대상). 처리 방법은 **Round 6** 참고.
  - cwd나 상위 폴더의 `.claude/commands/*.md`(스킬처럼 로드되는 custom command 파일)도 실행 전에 거부합니다.
  - 실행 후 추가 검사: init의 `apiKeySource`가 `"none"`이어야 합니다(`ApiKeySource` 문서: "`none` — No API key. The session authenticates another way, such as a claude.ai login"). `apiKeyHelper`·`ANTHROPIC_API_KEY`·`/login managed key`이거나 필드가 없으면 그 run을 오류로 기록하고 멈춥니다. CLI stream-json init이 SDK와 같은 필드를 낸다는 것은 **(추정)**, 첫 실행에서 확인.

#### Subscription auth (구독 인증만)
실제 실행은 **구독 인증으로만** 합니다. Claude Code는 claude.ai OAuth(구독 플랜), Codex는 ChatGPT 로그인을 씁니다. API 키는 어떤 경로로도 쓰지 않고, 사용자의 기존 로그인도 바꾸지 않습니다.
- **API 키 변수 거부 + 제거**: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`, `CODEX_API_KEY` 중 하나라도 설정돼 있으면(빈 값 포함) 실행을 거부합니다. `run`·`loop`는 preflight에서, `grade`·`calibrate`는 `--live` 게이트에서 거부합니다. 그와 별개로 모든 자식 프로세스 환경(`live_exec`, run별 sandbox 환경, `claude auth status`)에서 이 변수들을 항상 지웁니다. 값은 읽지도 출력하지도 않습니다. run의 격리 기록(`isolation.env_removed`)에는 지운 변수 이름만 남깁니다.
- **Claude — 기존 Keychain 로그인 한 가지** (Round 6. 사용자가 따로 할 일 없음: `claude setup-token`, 새 로그인, 토큰 export 모두 필요 없음)
  - HOME·`CLAUDE_CONFIG_DIR`을 그대로 두므로 지금 세션의 claude.ai(OAuth 구독) Keychain 로그인을 씁니다(위 Claude Code 항목의 근거). SSH 세션이나 데스크톱 밖에서 시작한 tmux 서버에서는 Keychain을 못 읽을 수 있습니다(추정: macOS 로그인 키체인 잠금 해제는 로그인 세션 기준). 그 경우 `claude auth status`가 로그인 없음으로 나와 실행 전에 멈춥니다.
  - `CLAUDE_CODE_OAUTH_TOKEN`이 셸에 export되어 있어도 거부하지 않고, 모든 자식 환경에서 **지웁니다**(`IGNORED_AUTH_ENV`). 그래야 run이 `claude auth status`가 확인한 바로 그 Keychain 로그인으로 인증합니다. 값은 읽거나 쓰지 않고, 격리 기록 `env_removed`에 이름만 남습니다.
  - **계정 자동 전환(2026-10-16)**: `claude`를 띄우는 모든 자식(`live_exec`, `claude auth status`, plugin list)에 호출자 marker `CLAUDE_AS_CALLER=skill_eval`과 opt-in `CLAUDE_AS_AUTO_SWITCH=1`을 넣습니다(`with_account_marker`). Codex 등 다른 CLI에는 넣지 않습니다. skill_eval 자신은 여전히 토큰을 넘기지 않습니다(위 `IGNORED_AUTH_ENV` 제거가 먼저). PATH의 `~/bin/claude` 계정 shim(claude-accts v6)이 marker를 보고, skill_eval 호출은 항상 acct0에서 시작합니다(cooldown·`CLAUDE_ACCOUNT`·순서 무시, `-p`가 아닌 호출은 acct0 고정). 그 `-p` 호출 자체가 usage limit으로 실패했을 때만, 그리고 대상 계정의 `claude auth status --json`이 loggedIn true + 구독(API 키 아님)일 때만 `~/.claude-acctN`으로 한 번 재시도합니다. 그때 그 계정의 Keychain 토큰은 shim이 자식 claude에만 넣고 `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`을 함께 설정해 평가 중인 에이전트의 Bash·hook이 토큰을 물려받지 않게 합니다(이 변수가 OAuth 토큰까지 지우는지는 문서 문구 "Anthropic … credentials" 기준 추정). 전환 대상 config dir(shim과 같은 입력: `CLAUDE_AS_HOME` 또는 ~, `CLAUDE_ACCOUNTS` 또는 모든 `.claude-acctN`)은 기본 config dir과 같은 preflight·launch 검사(settings·remote 캐시 인증 설정, 같은 이름 사용자 스킬, synced 스킬)를 받습니다(`switch_account_config_dirs`). 끄려면 `SKILL_EVAL_ACCOUNT_SWITCH=0`(marker는 남고 shim이 acct0로 고정). marker가 없으면 shim은 예전 부모 프로세스 추정으로 acct0 고정만 합니다. 주의: `claude auth status` 게이트는 그 시점에 쓰일 계정 하나만 확인합니다. 테스트: `tests/test_account_switch.py`.
  - **확인 (`--live` 게이트, dry run에서는 실행 안 함)**: `claude auth status --json`을 실행합니다(모델 호출 아님, 과금 없음).
    - API 키 계열 값(`api_key`, `apiKeyHelper`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `console`)이 이름에 auth/method/source/provider가 들어간 키 어디에든 있으면 → 거부
    - 통과 조건은 둘 다: **`loggedIn: true`**(불리언 `true`만. 필드 없음, `"true"` 문자열, `1`은 거부) **그리고** 이름에 `method`가 들어간 키(예: `authMethod`, `auth.method`)가 하나 이상 있고 그 값이 **모두** 인정 목록(`CLAUDE_SUBSCRIPTION_METHODS`: 정규화한 `claudeai`, `oauth`, `oauthtoken`, `claudeaioauth`, `subscription`)과 정확히 같음. 다른 키(`provider` 등)에 oauth 비슷한 문자열이 있거나 `my-oauth-thing`처럼 일부만 겹치는 것은 인정하지 않습니다. `claude.ai` 외 철자는 **(추정)** 입니다.
    - 그 밖(예: Bedrock), JSON이 아님, 종료코드가 0이 아님, `loggedIn: false` → 거부(fail closed)
    - 출력 원문은 저장하거나 출력하지 않습니다. 오류에는 방법 이름만 넣고, 이메일 같은 값은 `<hidden>`으로 가립니다.
  - 근거: 로컬 Claude Code changelog(`~/.claude/cache/changelog.md`)의 세 항목입니다. "Added `claude auth login`, `claude auth status`, and `claude auth logout` CLI subcommands", "Added `configDirectory` to the output of `claude auth status --json`", "Fixed `claude auth status` reporting a Console sign-in's stored API key as `claude.ai`; it now reports `api_key`".
  - **JSON 키 이름(`authMethod`, `loggedIn` 등)은 확인하지 못했습니다(추정)**. 이 세션에서 `claude auth status` 실행이 허용되지 않았기 때문입니다. 형식이 다르면 첫 `--live`가 실행 전에 fail closed로 멈춥니다. 그때 사용자가 `claude auth status --json` 출력의 키 이름을 보고 맞추면 됩니다.
  - 위 수정 이전 버전 CLI는 Console 로그인에 저장된 API 키를 `claude.ai`로 보고할 수 있습니다(changelog 기준). 환경변수는 따로 막지만, 그런 저장 키는 이 검사로 못 걸러낼 수 있습니다(추정).
- **Codex — ChatGPT 로그인**
  - `$CODEX_HOME/auth.json`(file 저장 방식)에서 `auth_mode` 필드**만** 파싱합니다. 토큰 필드는 반환하지도 출력하지도 않습니다. 값이 `chatgpt`여야 하고, `apikey`이거나 필드가 없거나 파싱에 실패하면 거부합니다. keyring 저장 방식이면 파일이 없어 거부합니다.
  - 이 PC 확인값(2026-10-15, 최상위 키 이름과 `auth_mode`만 확인): `auth_mode`는 `chatgpt`, 파일 권한은 0600.
  - run마다 sandbox `codex_home/`(0700)에 **읽기 전용 사본(0400)** 을 `O_EXCL`로 새로 만듭니다. 실행이 끝나면 sandbox와 함께 항상 지웁니다(오류, 격리 실패, sandbox 생성 중 실패도 포함). 원본은 읽기만 하며 내용과 권한이 그대로입니다(테스트로 확인).
  - **위험: 이 사본은 시험 중인 에이전트가 읽을 수 있습니다.** 하네스 자신은 사본 내용을 출력하거나 기록하지 않지만, Codex 에이전트는 같은 사용자로 실행되므로 0400 권한으로는 막히지 않고, Codex `workspace-write` 샌드박스도 파일 읽기를 허용합니다(https://github.com/openai/codex/blob/main/codex-rs/prompts/templates/permissions/sandbox_mode/workspace_write.md, Codex r5 리뷰 인용). 에이전트가 사본을 읽으면 그 토큰이 저장되는 최종 답(`final.md`), transcript, `run-k/workdir/` 결과물에 들어갈 수 있습니다. 그래서 "절대 기록되지 않는다"고 말할 수 없습니다. 이 위험 때문에 **첫 실제 실행에서 Codex는 제외**하고, `--strict-budget` 기본값이 Codex `--live`를 거부합니다.
  - 심볼릭 링크는 쓰지 않습니다. 링크에 chmod를 하면 사용자 원본의 권한이 바뀌기 때문입니다. 이전 방식(링크 + 이상 시 sandbox 보존)은 비밀 사본을 임시 폴더에 남길 수 있어서 바꿨습니다.
  - 실행 뒤 사본이 바뀌었거나 없어졌으면(토큰 갱신으로 추정) iteration을 멈추고 안내합니다. 비교는 메모리 안 sha256으로만 하고 해시는 어디에도 쓰지 않습니다.
  - 갱신된 사본은 sandbox와 함께 지워집니다. 갱신 토큰이 회전형이면 원본이 무효가 될 수 있습니다(추정). 그때는 `codex login status`로 확인하고 필요하면 `codex login`을 다시 합니다.
- **남는 한계(추정)**
  - 구독 인증에서도 Claude `--max-budget-usd`가 API 환산 비용 기준으로 동작하는지 확인하지 못했습니다.
  - 플랜 사용 한도에 걸리면 run은 오류로 기록됩니다. 하네스는 플랜 한도를 모릅니다.
  - (Round 5에서 해결) 제3자 제공자·게이트웨이 변수(`CLAUDE_CODE_USE_BEDROCK` 등)도 이제 거부하고 지웁니다. 아래 **Round 5** 참고.

#### Round 6 (사용자 결정 반영, Codex r5 리뷰)
- **첫 실제 실행은 Claude만**: Codex는 위 auth.json 사본 위험과 호출당 상한 부재 때문에 제외합니다. 첫 실행 명령은 `--engines claude --runs 1`이고, `--strict-budget` 기본값 때문에 `--engines`에 codex가 있으면 실행 전에 거부됩니다.
- **`--strict-budget` 기본 ON, `--no-strict-budget`은 위험한 opt-out**: 위 호출당 예산 제어 참고. `plan` 출력에 `budget enforcement: STRICT (default): --live refuses codex` 줄이 나옵니다.
- **Claude 인증 = 기존 Keychain 로그인**: `claude auth status --json`이 `loggedIn: true`이고 method가 claude.ai/OAuth 구독이어야 합니다(API 키 계열은 계속 거부). 사용자에게 아무것도 요구하지 않습니다(`claude setup-token`·로그인 안내 없음). `CLAUDE_CODE_OAUTH_TOKEN`은 자식 환경에서 지웁니다.
- **공유 config dir의 사용자 수준 스킬 처리**: Keychain은 config dir에 묶여 있어 run마다 빈 config dir을 줄 수 없습니다. 그래서 —
  - claude.ai synced 스킬 중 `user-invocable: false`(또는 SKILL.md를 못 읽음): init `skills` 목록에 나오지 않아 실행 후 검사로 못 잡으므로 **실행 전에 거부**합니다(preflight와 run 직전 검사). 이 PC에는 0개(2026-10-15, 이름·개수만 확인).
  - 그 밖의 synced 스킬: 로드되면 init `skills`에 나오므로 실행 후 정확 집합 검사가 그 run 뒤에 iteration을 멈춥니다(그 run 비용은 발생).
  - 플러그인 스킬(`user-invocable: false` 포함): 활성화된 플러그인을 통해서만 로드되고, init `plugins`가 명시적 빈 목록이 아니면 멈춥니다. 플러그인이 로드되면 init `plugins`에 반드시 나온다는 것은 **(추정)**.
  - `plan`에 "isolation notes (not blocking)"로 개수를 보여 줍니다. 이 PC(2026-10-15 dry-run 출력): synced 33, plugin 150.
- **호출 뒤 파싱 실패도 차감**(P2): 실행·grader·builder 호출이 끝난 뒤 출력·사용량 파싱이 실패하면(예: 숫자가 아닌 `usage`) 예외가 새지 않고, **max(reported, cap)** 를 차감한 뒤 멈춥니다. 실행은 `timing.json`에 기록하고 오류 "output could not be parsed after the call (<예외 이름>)", 결과물 복사·init 검사 같은 호출 뒤 처리 실패는 "output handling failed after the call (…)"입니다. grader·builder는 `cost.json`에 기록합니다.
- **auth.json이 밖을 가리키는 symlink로 바뀐 경우**(P2): 삭제 경로를 realpath로 풀지 않고 글자 그대로(lexically) 검사해 **링크 자체만 unlink**합니다(대상 파일은 지우거나 비우지 않음). `codex_home` 폴더 자체가 밖을 가리키는 링크로 바뀌면 따라가지 않고 보고합니다. 사본 삭제와 별도의 `finally`에서 sandbox 트리 삭제를 실행하므로, 사본 삭제가 실패하거나 예외를 내도 임시 폴더는 항상 지워집니다.
- **`--live --dry-run`**: 그 실제 명령이 만날 모든 live 게이트(검증, strict budget, API 키 변수, 승인, 가격, preflight, 예산)를 첫 실패에서 멈추지 않고 모두 나열하고, `--dry-run`만 뺀 실제 명령을 출력합니다. 모델 호출, `claude auth status` 실행, 파일 쓰기를 하지 않습니다(`_LIVE`도 켜지 않음). 게이트가 모두 통과하면 종료코드 0, 아니면 3. `--live` 없이 `--dry-run`만 주면 오류입니다(그때는 원래 dry run).

#### Round 7 (Codex r6 리뷰)
- **읽을 수 없는 builder 출력도 차감 후 중단**(P2): builder 출력 전체가 JSON 객체가 아니면(빈 출력, 잡음, 배열 등) 정상 반환하지 않고 **max(reported, cap)** 를 차감·`cost.json`에 기록한 뒤 "builder output is not a JSON object … charged $X and stopped"로 멈춥니다. reported는 출력 중 JSON 줄의 가장 큰 `total_cost_usd`라, 한 줄이 cap보다 큰 비용을 보고하면 그 값이 차감됩니다.
- **읽을 수 없는 Codex 스트림도 실패 호출**(P2): `codex exec --json` 출력에 `type`이 있는 JSON 이벤트가 한 줄도 없으면(빈 출력 포함) 파싱 오류로 처리합니다. 그 run은 "output could not be parsed after the call (ValueError)"로 기록되고, max(reported, 호출당 최악 비용 상한)을 차감한 뒤 iteration을 멈춥니다.
- **settings `env`의 `CLAUDE_CODE_OAUTH_TOKEN`도 거부**(P2): 셸 환경의 토큰은 자식 환경에서 지우지만, settings 소스의 `env`는 CLI가 스스로 적용하므로 지울 수 없습니다. 그래서 run이 읽을 수 있는 모든 settings 소스(사용자 settings(.local).json, run-root와 상위 폴더·sandbox의 project/local, managed, server-managed 캐시)의 `env`에 이 키가 있으면 API 키와 같이 실행 전에 거부합니다(키 이름만 확인, 값은 읽어 출력하지 않음). 그 토큰이 실제로 Keychain 로그인보다 우선하는지는 확인하지 못했습니다 **(추정)** — 확인하지 않고 막습니다.
- **live command 출력은 `shlex.join`**(P3): `--live --dry-run`이 출력하는 실제 명령은 셸 따옴표 규칙으로 만듭니다. `$`, 백틱, 작은따옴표가 든 인자도 그대로 복사해 실행할 수 있습니다(이전 JSON 따옴표는 `$`·백틱이 셸에서 해석됨).

#### Round 8 (Codex r7 리뷰)
- **끝 이벤트가 없는 Codex 스트림도 실패 호출**(P2): 읽을 수 있는 이벤트가 있어도 `turn.completed`나 `turn.failed`가 한 줄도 없으면(예: `thread.started`만 있고 끊긴 스트림, `error` 이벤트만 있음) 파싱 오류로 처리합니다. 종료코드가 0이어도 그 run은 "output could not be parsed after the call (ValueError)"로 기록되고, max(reported, 호출당 최악 비용 상한)을 차감한 뒤 iteration을 멈춥니다.
- **result가 아닌 builder JSON 객체도 차감 후 중단**(P2): builder 출력이 JSON 객체여도 `type`이 `"result"`가 아니면(예: `{}`) 정상 반환하지 않고 **max(reported, cap)** 를 차감·`cost.json`에 기록한 뒤 "builder output is not a result object … charged $X and stopped"로 멈춥니다.

#### Round 9 (Codex r8 리뷰)
- **`is_error: true`인 builder result도 차감 후 중단**(P2): builder 출력이 `type: "result"`여도 `is_error`가 참이면(종료코드 0 포함) 성공으로 취급하지 않고 **max(reported, cap)** 를 차감·`cost.json`에 기록한 뒤 "builder result is an error (subtype …) … charged $X and stopped"로 멈춥니다. loop는 `builder-failed`로 끝납니다.

#### Round 10 (첫 실제 실행 설정: Claude Sonnet 5.5 effort high 시험, Codex 채점)
- **시험 대상 effort**: `--claude-effort low|medium|high|xhigh|max` → `claude -p … --effort <level>`(Claude Code changelog의 `--effort` 플래그). `--claude-arg=--effort`는 거부합니다(모델·예산처럼 하네스가 정함). `plan.json`에 `efforts`가 기록되고 plan에는 `claude(claude-sonnet-5-5, effort high)`처럼 표시됩니다. `--codex-effort`도 있지만 Codex는 시험 대상이 아닙니다.
- **채점 엔진**: `--grader-engine claude|codex`(기본 claude), `--grader-effort`. codex를 고르면 LLM 채점과 블라인드 pairwise 판정을 모두 `codex exec --json --skip-git-repo-check --ephemeral --sandbox read-only`로 합니다. 모델은 Codex CLI 기본값(`--grader-model`을 주면 `-m`). effort는 `-c model_reasoning_effort=<level>`로 넘기는데, **이 config 키 이름은 1차 출처로 확인하지 못했습니다(추정)** — 실제 실행 전에 `codex exec --help`·Codex config 문서로 확인하세요. 허용 값 목록(none/minimal/low/medium/high/xhigh)은 로컬 Codex plugin runtime 문서에서 옮겼습니다. `--output-schema`도 미확인이라 쓰지 않고, 스키마를 프롬프트에 넣어 마지막 agent 메시지가 JSON 객체 하나(또는 ```json 블록)이게 하고 파싱합니다.
- **Codex는 채점자로만**: `--strict-budget` 기본값은 여전히 `--engines`의 codex를 거부합니다. Codex 채점자는 codex 실행이 들어 있는 iteration을 채점하지 않습니다(`check_grader_model`). Claude 채점자가 시험 모델과 같으면(예: 기본 채점 모델 `claude-sonnet-5-5` = 시험 모델) `run`·`loop --live`가 실행 전에 거부합니다.
- **읽기 전용·격리**: 실행 직전 명령 모양을 검사해 `--sandbox read-only`가 정확히 하나가 아니거나 `--full-auto`·`--dangerously-bypass-approvals-and-sandbox`·`--add-dir`가 있으면 호출하지 않습니다. cwd는 매번 새 빈 임시 폴더, HOME/CODEX_HOME은 Codex 실행과 같은 격리 폴더(사용자 config.toml·스킬·AGENTS.md 안 보임)이고, 호출 뒤 auth 사본 변경 비교 → 사본 삭제·확인 → 폴더 삭제 순서로 정리합니다(정리 실패·사본 변경은 차감 후 중단).
- **auth.json 사본 위험(채점 경로에도 있음)**: Codex 채점자도 ChatGPT 로그인 `auth.json`의 읽기 전용 사본(0400)을 격리 CODEX_HOME에 둡니다. **read-only sandbox는 읽기를 허용하므로 채점 에이전트가 이 사본을 읽을 수 있습니다.** 게다가 채점 프롬프트에는 시험 중인 run의 transcript(신뢰할 수 없는 입력)가 들어가므로, 프롬프트 주입으로 채점자가 토큰을 `grading.json` evidence나 `comparison-k.json` reasoning에 옮겨 적을 수 있습니다. 프롬프트에 "도구·파일 읽기 금지"를 넣었지만 이것은 강제가 아닙니다. 하네스 자신은 사본 내용을 출력·기록하지 않습니다. plan/dry-run에 `grader isolation: …READABLE by the grader agent…` 줄로 표시됩니다.
- **보수적 호출당 차감**: Codex에는 호출당 상한이 없으므로, 채점 호출은 최악 비용 상한(가정 토큰 × `WORST_CASE_FACTOR` 3 × 단가)이 남은 예산 안일 때만 시작하고, 끝나면 **max(사용량 × 단가, 상한)** 을 차감합니다. 실패(실행 뒤 예외, 종료코드≠0, 스트림 잘림, JSON 아님)도 같은 금액을 차감하고 멈춥니다. 단가는 `--price grader=IN,OUT`이 없으면 `CODEX_GRADER_ASSUMED_PRICE` = PRICES 표에서 가장 비싼 행($10/$50 per MTok)을 씁니다. **OpenAI 단가는 확인하지 못했으므로 이 값이 실제 단가의 상한이라는 보장은 없습니다(추정).** 추정(estimate)도 Codex 채점 호출을 상한 금액으로 계산합니다. 여전히 soft limit입니다(plan의 `grader budget: SOFT LIMIT` 줄).
- **인증 확인**: `grade`·`calibrate`·`loop --live`에서 Codex 채점자를 쓰면 `codex_auth_problems()`(auth.json의 `auth_mode` 필드만 읽음)를 확인하고, Claude 채점자가 아니면 그 때문에 `claude auth status`를 부르지는 않습니다(시험 엔진·builder가 Claude면 확인). `--dry-run`은 둘 다 실행·읽기하지 않고 건너뛴다고 표시합니다.

**참고한 표준 방식**: skill-creator `scripts/run_eval.py`는 격리를 거의 하지 않습니다 — `.claude`가 있는 프로젝트 루트를 cwd로 쓰고 `.claude/commands/<name>.md`를 잠깐 만들었다 지우며, 환경에서 `CLAUDECODE`만 뺍니다(https://github.com/anthropics/skills/blob/main/skills/skill-creator/scripts/run_eval.py, 조사 에이전트 확인). agentskills.io는 "Each eval run should start with a clean context — no leftover state from previous runs or from the skill development process."라고만 합니다(https://agentskills.io/skill-creation/evaluating-skills). 이 하네스의 sandbox·사용자 스킬 차단·init 검사는 그보다 엄격한 자체 규칙(H)입니다.

**채점**
- 순서: static(정규식·도구 호출·트리거·파일) → llm → human. LLM 채점자는 그 iteration의 `plan.json`에 기록된 실행 모델과 **다른 모델**이어야 합니다(같으면 오류, Claude 실행 모델을 모르면 오류, `plan.json`이 없으면 오류 — `grade` 명령의 `--claude-model`은 보지 않음). 기본 실행 `claude-opus-5-5`, 채점 `claude-sonnet-5-5`. 채점은 `claude -p --json-schema ... --tools ""`로 도구 없이 yes/no와 근거를 받습니다.
- `evals.json`의 선택 필드 `claude_python_script`는 스킬 폴더의 `scripts/*.py` 하나를 가리킵니다. 두 arm(`with_skill`, `without_skill`)의 생성 명령은 모두 `--permission-mode default --allowedTools "Edit(/**),Bash(python3 -I .claude/skills/<name>/<script> *)" --disallowedTools Monitor PowerShell`을 사용합니다. `Edit(/**)`는 CLI 인자의 단일 `/`이 세션의 기본 작업 폴더에 고정되는 경로 규칙으로, 두 arm의 평가별 workdir 안에서만 파일 편집을 사전 승인합니다. [Claude 권한 문서](https://code.claude.com/docs/en/permissions)에 따르면 `Edit` 규칙은 Write·Edit·MultiEdit·NotebookEdit 등 내장 파일 편집 도구에 적용되며, `//`만 파일시스템 절대경로입니다. 지정 Bash 호출도 사전 승인하고, 금지 규칙은 알려진 다른 명령 실행 도구를 제거합니다. 두 설정의 `--settings` JSON에는 모든 도구 이름과 일치하는 `PreToolUse`/`*` 훅을 넣습니다. 작업 폴더 밖 하네스 소유 `claude_bash_gate.py`는 입력에 `command` 필드가 있는 현재 또는 미래의 도구 호출을 차단하며, Bash의 정확한 `python3 -I` 단일 호출과 작업 폴더 내부의 새 출력 경로만 허용합니다. 파일 편집 도구(Write, Edit, MultiEdit, NotebookEdit)와 그 밖에 `file_path` 또는 `notebook_path`를 담은 도구는 훅이 모든 대상 경로를 실경로로 해석합니다. workdir 밖, `.claude/skills/**`, 하네스 훅 자체와 설치된 스킬 스크립트의 편집은 거부하고, workdir 안의 일반 파일 편집은 기존 권한 검사에 맡깁니다. Read, Glob, Grep, Skill, TodoWrite 같은 비편집 도구에는 훅이 결정을 내리지 않습니다. 실패한 훅도 `onFailure: block`으로 차단합니다. `"disableAllHooks": false`가 프로젝트·로컬의 훅 비활성화 설정보다 우선하도록 하고, managed 정책으로 훅이 막히거나 `allowManagedPermissionRulesOnly`가 허용 규칙을 버리면 실행 전 거부합니다. `acceptEdits`는 사용하지 않습니다. 스킬 문서가 동일한 상대경로와 `python3 -I`를 사용해야 매치됩니다. `file_equals_fixture`는 산출물과 평가 입력의 원본 바이트를 비교하고, `csv_cleaned`는 CSV 전체를 읽어 공백 정리·완전 일치 중복 제거 결과 및 지정한 데이터 행 수를 비교합니다. 두 검사는 작업 폴더 밖으로 향하는 심볼릭 링크를 거부합니다.
- pairwise: 엔진·케이스·run k마다 with_skill vs 기준 출력을 A/B로 보여 주고, 순서는 `random.Random(seed)`로 섞어 `comparison-<k>.json`에 `mapping`과 `seed`를 남깁니다(`--seed`로 재현).
- human: `human_review.json`의 `passed`를 true/false로 채우고 `grade`를 다시 돌리면 grading.json에 반영됩니다. 채우기 전에는 pass_rate 분모에서 빠지고 `pending`으로 셉니다.
- 집계: skill-creator `aggregate_benchmark.py`와 같은 통계(mean, 표본 stddev(n-1), min, max, 소수 4자리), delta는 `with_skill - 기준` 문자열(`"+0.50"`). 노트: 모든 설정에서 늘 통과/늘 실패하는 assertion, run 간 stddev ≥ 0.3(H)인 eval.

**승인 게이트**
| 게이트 | 언제 필요 |
|---|---|
| `requirements` (`<workspace>/requirements.md`) | 항상 — 승인 없으면 `run --live`/`loop` 거부 |
| `eval_set` (evals.json + trigger_evals.json + `evals[].files`의 모든 입력 파일) | 항상 — 같음. 입력 파일이 바뀌거나 없어져도 승인 무효 |
| `iteration-N` 결과 | 문제가 있을 때만(`gate.json`의 `needs_approval`). 문제: ① 회귀 — with_skill 평균 pass_rate가 기준 설정보다 낮거나 이전 iteration보다 낮음(허용 오차 0, H), ② 채점자 일치율 < 0.8(H), ③ with_skill 실행의 static 검사 실패, ④ 미완료 — `stopped`(예산 등으로 조기 중단), `plan.json`의 기대 run 수(케이스 × 설정 × runs, 트리거 쿼리 × runs × 스킬 있는 설정)보다 채점된 run·트리거 run이 적음, `plan.json` 없음, ⑤ 실행 실패 — 오류가 난 eval run·트리거 run, ⑥ with_skill 트리거 기준 실패 쿼리, ⑦ 미채점 — **각 run의 `grading.json`에서** `passed`가 true/false가 아닌 항목(LLM 미채점, human 대기)을 셉니다. `human_review.json` 큐만 채우고 `grade`를 다시 돌리지 않으면 계속 미채점입니다, ⑧ 미완료 — 블라인드 pairwise 비교가 기대 개수(케이스 × runs, 설정이 둘일 때)보다 적음(정적 항목만 있는 dry grade도 여기서 걸림), ⑨ `stale` — 시험 당시(`plan.json`) 스킬 폴더 해시나 평가셋 해시가 지금과 다름, `unbound` — 해시 기록이 없는 옛 iteration |
| `registration` | 마지막 iteration에 문제가 있을 때만. 승인과 별개로 스킬 폴더·평가셋 해시가 시험 당시와 같아야 등록됨 |
보정을 안 한 것만 노트입니다. `loop`는 최대 5회(`MAX_AUTO_ITERATIONS`, 범위 밖이면 오류)이고, 5회 뒤에도 문제가 남으면 `needs-approval`로 끝납니다. 남은 문제가 human 리뷰 대기뿐이면 builder를 부르지 않고 `needs-human-review`로 끝납니다.
- **스킬 해시**: 스킬 폴더 전체에서 실행에 설치되는 파일(`evals/`, `__pycache__`, `*-workspace` 제외 — 설치와 같은 규칙, 심볼릭 링크는 내용으로)을 상대경로와 함께 sha256. `plan.json`의 `skill_sha256`·`eval_set_sha256`(+ `old_skill_sha256`)에 run 시작 시 기록됩니다.

**첫 실제 eval 대상 (제안)**: 이전 계획의 `dev-setup`은 pinesky-skills 저장소와 함께 삭제되어 대상에서 뺐습니다. 대신 이 저장소의 `golden/skills/csv-cleaner`를 제안합니다. 이유: 저장소 안에 있어 사라질 위험이 없고, 같은 이름의 설치본이 없으며(preflight 문제 0, 2026-10-15 dry-run), `scripts/clean.py`가 결정적이라 결과를 기계로 확인하기 쉽고, 입력 파일(`evals/files/*.csv`)이 이미 있습니다. 실행 전에 할 일: ① (Round 6 완료) `evals/trigger_evals.json`(10+10, near-miss 음성 포함) 작성, ② (Round 10 완료) 문자열 assertion 일부를 `static` 검사 객체로(eval 1 `file_exists orders_clean.csv`, pressure 케이스 4–6 포함: static 6 / llm 9 / human 0), ③ (Round 6 완료) eval 1 프롬프트 경로를 실제 입력 위치 `evals/files/orders_2025.csv`로 맞춤, ④ `requirements.md` 작성과 `requirements`·`eval_set` 승인(사용자), ⑤ 첫 실행은 `--engines claude --runs 1`(Codex 제외)로 init 이벤트 필드(격리 검사) 확인 — 실행 전에 같은 명령을 `--dry-run`으로 확인. `style-lab`은 `disable-model-invocation`이라 트리거 평가가 의미가 없고, `find-skills`는 `~/.agents/skills`에 원본만 있어(저장소 없음) 후순위입니다.

### Cost
구독 인증 실행은 토큰당 과금이 없습니다. 그래서 하네스는 아래 두 값을 **따로** 기록합니다.
- **API 환산 비용(API-equivalent)**: 같은 호출을 API 단가로 냈다면 들었을 금액입니다. 호출마다(실행·트리거·채점·builder 모두 같은 함수 `api_equivalent`) 다음 순서로 정합니다.
  1. 사용 토큰 × API 단가(`PRICES`/`--price`) → `tokens_x_price`
  2. CLI 보고값(Claude `total_cost_usd`)은 **1보다 클 때만** 씁니다 → `cli_reported`. 보고값 0(또는 음수·NaN·숫자 아님)은 무시하므로, 보고값 0이 0이 아닌 토큰 사용량을 가리지 못합니다. 구독 인증에서도 API 단가 환산값을 보고한다는 것은 추정
  3. 토큰 사용량을 알 수 없으면(사용량은 있는데 단가를 모를 때 포함) 그 호출에 걸었던 상한(cap/bound)이 기준입니다. 보고값이 있어도 **max(reported, cap)** 를 차감합니다(Round 5: 작은 보고값이 상한을 덮지 못함). 상한이 크면 → `cap_charged`, 보고값이 크면 → `cli_reported`
  4. 실행 뒤 실패한 호출(타임아웃·예외, "call failed after launch")도 상한을 차감하고 기록한 뒤 멈춥니다(아래 **Round 5**). 호출 뒤 출력·사용량 파싱 실패도 같으며 max(reported, cap)를 차감합니다(**Round 6**)
  - **`--budget-usd`는 이 값의 한도이고, 예산 게이트도 이 값으로 판단합니다**(호출 전 상한·호출 후 차감·예상 비용 ≤ 예산 모두).
- **실제 지출(actual spend)**: `actual_spend_usd: null`입니다. 플랜 요금이나 추가 사용 과금은 하네스가 볼 수 없어 계정에서 확인해야 합니다. 0으로 적지 않습니다.
- 기록 위치
  - run별 `timing.json`: `cost_usd`(CLI 보고 원값), `api_equivalent_usd`, `api_equivalent_source`(`cli_reported`|`tokens_x_price`|`cap_charged`|`unknown`), `billing: "subscription"`
  - `plan.json`: `api_equivalent_usd`(그 `run`의 실행+트리거 합, 이전 `spent_usd`를 대체), `billing`
  - iteration의 `cost.json` `entries[]`: 채점(`grade`), 보정(`calibrate`), builder 호출(호출 수, 토큰, `api_equivalent_usd`). 중간에 멈춰도 기록합니다. builder 비용은 그 builder가 읽은 iteration에 붙으므로, 그 iteration의 `report`를 다시 돌려야 benchmark에 들어갑니다.
  - `benchmark.json` `cost`: `billing`, `budget_basis: "api_equivalent"`, `api_equivalent_usd{by_engine{exec,trigger}, other{grade,calibrate,builder}, total}`, `runs_without_usage`, `actual_spend_usd: null`, `actual_spend_note`
  - `comparison.md`: 위쪽 한 줄과 `## Cost` 표(실제 지출 = not metered)
  - 실행 출력 문구: "API-equivalent ~$X (subscription: not billed per token)"

**비용 추정**(`estimate`/`plan`, "API-equivalent total"): 호출 수 = 실행(케이스 × 설정 2 × runs) + 트리거(쿼리 × runs × 스킬 있는 설정 수) + 채점(llm 항목 있는 케이스 × 설정 2 × runs) + pairwise(케이스 × runs), 엔진마다. `run --live --dry-run`의 예산 게이트는 실제로 수행하는 실행·트리거만, 별도 `grade --live --dry-run`은 채점·pairwise만 계산합니다. 호출당 토큰은 **가정값**입니다(exec 60k/4k, trigger 25k/500, grader 12k/800, pairwise 16k/800 — 측정값 아님, 파일럿 뒤 `--tokens`로 교체). 입력 토큰은 전부 기본 입력 단가로 계산해 캐시 할인을 빼므로 상한 쪽 추정입니다. Claude 단가는 위 요금 페이지(2026-10-15)에서 옮긴 값이고, Codex/OpenAI 단가는 1차 출처로 확인하지 못해 넣지 않았습니다(`--price codex=IN,OUT` 필요, 없으면 `--live` 거부).

### skill-creator 대비 차이(deviation)
- **엔진 계층 추가**: skill-creator는 `iteration-N/eval-*/<config>/run-*`입니다. 하네스는 엔진이 둘이라 `iteration-N/<engine>/eval-*/...`로 한 단계 더 둡니다. 엔진 폴더 안은 skill-creator 구조 그대로이고 엔진별 `benchmark.json`도 skill-creator 스키마입니다. skill-creator `scripts/aggregate_benchmark.py iteration-N/<engine>`은 모든 run에 채점된 항목이 하나 이상 있을 때 동작합니다(가짜 엔진 결과로 확인). 아직 채점 안 된 run(`pass_rate: null`, 예: human 항목만 있는 케이스의 리뷰 전)이 있으면 그 스크립트는 `TypeError`로 멈추므로 하네스의 `report`를 쓰세요. 최상위 `iteration-N/benchmark.json`은 같은 필드에 `per_engine`, `trigger`, `pairwise`, `static_failures`, `pressure`, `human_pending`, `llm_not_graded`, `calibration`, `gate`를 더하고 `runs[]`에 `engine`을 더한 확장입니다.
- **eval 폴더 이름**: skill-creator `eval-<ID>`/설명 이름, agentskills.io `eval-<설명>`. 하네스는 `eval-<id>-<name>`(name 없으면 프롬프트 slug)이고 eval_id는 `eval_metadata.json`에 둡니다(aggregate_benchmark가 읽는 위치).
- **grading.json**: `expectations[]`의 `text`/`passed`/`evidence`는 같고 `id`, `layer`, `status`, `grader`를 더합니다. `passed`는 미채점(human 대기, dry-run llm)일 때 `null`이고 `summary.pass_rate` 분모에서 빠지며 `summary.pending`에 셉니다(skill-creator는 bool만). `claims`, `user_notes_summary`, `eval_feedback`는 만들지 않습니다. agentskills.io 예시는 `assertion_results`라는 이름을 쓰지만 skill-creator 뷰어가 `expectations`를 읽으므로 그쪽을 따랐습니다.
- **benchmark.json delta**: skill-creator 형식(문자열 `"+0.50"`)을 따릅니다. agentskills.io 예시는 숫자(`0.50`)입니다. 실행 결과에 `pending`, `cost_usd`를 더하고, 채점 항목이 0개인 run은 pass_rate 통계에서 뺍니다(skill-creator는 0.0으로 넣음).
- **timing.json**: `total_tokens`, `duration_ms`, `total_duration_seconds`, `executor_start/end`는 같고 `input_tokens`, `output_tokens`, `cached_input_tokens`, `cost_usd`를 더합니다. 시간은 하네스가 CLI 프로세스를 감싸 잰 벽시계 시간이고, 토큰은 CLI 출력에서 읽습니다(subagent 알림이 아님). 채점자 시간은 넣지 않습니다.
- **comparison.json** 실패 기록(Round 11): 채점자가 A/B/TIE 이외의 `winner`를 주면 TIE로 바꾸지 않습니다. 호출 비용은 청구되고, `comparison-k.json`에 `status: "failed"`, `winner: null`, `invalid_winner`, `error`를 남긴 뒤 채점을 멈춥니다(benchmark 집계에서는 빠짐).
- **comparison.json**: `winner`(A/B/TIE)와 `reasoning`은 같고, comparator.md의 `rubric`·`output_quality` 점수 대신 `mapping`, `winner_configuration`, `seed`, `grader_model`을 둡니다. 비교 단위는 같은 번호 run끼리입니다.
- **사람 리뷰**: agentskills.io `feedback.json`(eval별 자유 의견) 대신 항목별 `human_review.json`(`passed` true/false + `note`)입니다.
- **트리거 평가**: 쿼리 파일 형식과 0.5 기준, 3회 실행은 같습니다. 개수는 정확히 10+10(H, 문서는 각 8~10). Claude 트리거 감지는 run_eval.py처럼 `.claude/commands/`에 명령 파일을 만들지 않고 스킬 폴더를 `.claude/skills/`에 설치해 실제 스킬로 띄웁니다. 스트림 조기 종료는 하지 않아 run 하나가 끝까지 돕니다(비용 추정이 이를 반영). train/validation 분할과 description 최적화는 없습니다.
- **채점 계층**: skill-creator assertion은 문자열뿐이고 채점 방식은 grader가 정합니다. 하네스는 항목마다 `layer`를 두고 static은 반드시 기계 검사로만 채점합니다(H).
- **승인 게이트·예산·회귀 판정·일치율 기준**은 skill-creator에 없는 자체 규칙(H)입니다.

## [분리 예정] 에이전트 평가
`agent master`로 옮길 때까지 동작은 그대로입니다.
- 에이전트: `<name>/AGENT.md` (frontmatter: name, description, goal, tools, success_criteria) + `scenarios/*.json`
  - 시나리오: `{"id": str, "turns": [{"user": str}, ...], "expected_tools": [str, ...], "must_include": [str, ...], "expected_outcome": str | [str, ...], "success_criteria"(선택): str | [str, ...]}`. 형식이 틀리면 `scenarios-valid` [필수] 실패
- 정적 검사: goal, 측정 가능한 success_criteria, 시나리오 2개 이상·expected_outcome, tools 선언과 expected_tools ⊆ tools, `## Tools`, `## Memory & Handoff`, 2턴 이상 시나리오
- `success-measurable` [필수]: success_criteria 중 자동 확인 가능한 것이 1개 이상. 자동 확인이 안 되는 기준(예: "모든 지적에 파일:줄 근거를 단다", "모든 주장에 출처를 단다")은 지우지 않고 그대로 두며, transcript 채점 때 needs-review로 나옵니다.
- transcript 채점([필수]): `transcript-valid`, `agent-valid`, `scenario-known`, `only-declared-tools`, `expected-tools-called`, `outcome-keywords`, `no-answer-lookup`, 결과 검증(검증된 출처 URL 개수, 파일:줄 근거 개수, 파일 변경 0건, 글자 수, 비교표). 확인 가능한 조건이 하나도 없으면 `outcome-evidence` 실패. 형식은 LOOP.md 참고
- 파일:줄 근거: 최종 답의 `경로:줄`(또는 `경로:시작-끝`)은 **그 줄이 도구 결과에 줄 번호와 함께 나왔을 때만** 셉니다. 범위 `경로:시작-끝`은 시작 <= 끝이어야 하고(`42-3` 불인정) 그 사이 **모든 줄**이 나왔어야 합니다. **줄은 그 파일 하나만 읽은 도구 호출의 `result`에서만** 셉니다. 인정하는 호출: ① 읽기 도구(이름에 `read`·`view`·`open`·`cat`, 검색·목록·통계·쓰기 계열 이름은 제외)에 경로 키(`path`, `file_path` 등) 하나, ② 셸 도구(`bash`, `shell` 등)의 명령 하나가 `cat -n FILE`·`nl -ba FILE` 또는 `grep -n PATTERN FILE`(파일 하나). 결과는 **전체가 줄 번호 형식**이어야 합니다: ①과 `cat -n`·`nl -ba`는 모든 줄이 `줄<탭|→|:>내용`(예: `cat -n`의 `%6d\t`, Read 도구의 `N→`)이고 구분자가 한 가지이며 줄 번호가 연속(N, N+1, …), `grep -n`은 모든 줄이 `줄:내용`·문맥 줄 `줄-내용`(`-H`면 `FILE:줄:내용`)이나 구분선 `--`이고 줄 번호가 증가. 한 줄이라도 형식이 다르면 그 결과의 줄은 하나도 세지 않습니다(예: 원문 그대로인 `200 OK\n404 Not Found`, 공백 구분 `42 x`). `cwd` 키가 있으면 상대경로를 그 기준으로 풉니다. 경로는 `./` 제거·정규화하고, 절대경로는 transcript `workspace` 안이면 그 기준 상대경로로 바꾼 뒤 **정확히 같을 때만** 같은 파일로 봅니다(뒷부분 일치 없음: `README.md`를 읽고 `docs/README.md:5`를 인용하면 불인정). 세지 않는 것: `echo`/`printf`로 찍은 줄, `wc`·`stat`·검색·쓰기 도구에 경로 키만 준 호출, 여러 파일(`cat -n a b`, `grep -rn`, `grep -n x a b`), `cat -b`/`cat -s`·기본 `nl`, 파이프·리다이렉트, top-level `tool_results`(어느 호출의 결과인지 모름), 다른 파일을 읽은 결과에 들어 있는 `경로:줄:내용` 줄, 파일 이름만 나오거나 없는 줄(예: `:99999`).
- `scenarios/` 폴더를 읽을 수 없으면(예: PermissionError) 예외로 죽지 않고 `scenarios-valid` 실패로 보고합니다.
- 예시: `golden/agents`(2), `bad/agents`, `transcripts/`. `code-reviewer-drill.json`은 고정 픽스처 `transcripts/fixtures/drill-repo/tests/test_sample.py`의 줄을 인용합니다(테스트 코드가 바뀌어도 인용 줄이 어긋나지 않음). 자동 검사를 모두 통과하고 needs-review가 남는 REVIEW 예시는 `research-followup-cited.json`, `code-reviewer-drill.json`. FAIL 예시는 `research-followup.json`, `bad/research-fabricated-urls.json`, `bad/code-reviewer-answer-lookup.json`

### 판정: PASS / REVIEW / FAIL
자동으로 판정할 수 없는 기준은 **needs-review**(사람 또는 모델 채점 필요)로 보고합니다. N/A로 조용히 빼거나 PASS로 치지 않습니다.
| 판정 | 조건 | 종료코드 |
|---|---|---|
| FAIL | 자동 검사 하나라도 실패([필수] 실패, 점수 80% 미만, 카테고리 하한 미달, 자동 검사 0개) | 1 |
| REVIEW | 자동 검사는 모두 통과했지만 needs-review 항목이 남음 | 종료코드 3 |
| PASS | 자동 검사 모두 통과, needs-review 없음 | 0 |
- needs-review 항목은 `status: "needs-review"`, `passed: null`이고 점수 분모에서 빠집니다. `--json` 출력의 `needs_review`에 id 목록이 나옵니다(id 예: `criteria-needs-review`, `expected-needs-review`).
- 사람/모델 채점자가 needs-review 항목을 확인해야 최종 PASS로 볼 수 있습니다. 그 채점 결과를 기록하는 기능은 아직 없습니다.

### 정답 조회 무결성 검사 (`no-answer-lookup`, [필수])
위 "정석" 원칙과는 다른, 실행 기록용 무결성 검사입니다. 에이전트가 문제를 직접 풀지 않고 정답을 찾아보면 FAIL입니다.
- 워크스페이스 밖 탐색: 정규화한 뒤 밖으로 나가는 `..`/`../x`, `~`, 파일시스템 루트 `/`, transcript에 `workspace`가 있으면 그 밖의 절대경로 (예: `find ..`, `find /`, `cat ../answer.txt`, `{"path": "../x"}`)
- git 이력 뒤지기: `git log --all`/`--reflog`/`-g`, `git rev-list --all`, `git reflog`, `git fsck`, `git cat-file`, `--lost-found`/`--unreachable`
- 검사 대상: 문자열 `input`(명령으로 봄), 객체 `input`의 경로 키(`path`, `file_path`, `dir`, `cwd`, `target` 등)와 명령 키(`command`, `cmd`, `script`, `args`). `pattern`, `query` 같은 검색어는 보지 않습니다.

## Known limitations

- Sentence splitting in description-when: a period directly followed by a lowercase letter (e.g. "Use when needed.the user wants a summary.") is not treated as a sentence boundary, so the condition is not cut there.

## 블라인드 라벨링 (`label_blind.py`, 2026-10-16)

`calibrate --golden`용 사람 라벨은 채점기 판정을 보지 않고 만듭니다. 이전 페이지(`label_v3_server.html`)는 Codex 판정을 보여 주고 라벨을 `agree ? grader_passed : !grader_passed`로 계산했으며 `fillAll`(모두 동의)이 있어 agreement 1.00이 순환적이었습니다.
- `python3 label_blind.py build <기존 페이지 또는 items JSON> <새 html>`: 채점기 필드(`grader`, `grader_passed`, `grader_evidence`, verdict 등, 중첩된 것까지)를 뺀 페이지를 새 파일로 만듭니다(기존 파일은 덮어쓰지 않음).
- `python3 label_blind.py serve <새 html> [--port 8765]`: `http://127.0.0.1:8765/`에서 열고, 사람이 항목마다 통과/불통과를 직접 고릅니다. 모든 항목에 답해야 저장되며(답은 항목 내용 fingerprint별로 브라우저에 보관), 같은 폴더에 `golden_labels_blind_<시각>.json`(`label` = `human_passed`, `blind: true`)으로 저장합니다. 모델 호출 없음.
- 테스트: `tests/test_binary_and_blind_label.py`.

