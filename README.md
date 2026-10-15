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

**안전 장치(기본은 dry run)**: `--live`와 `--budget-usd`가 둘 다 없으면 어떤 CLI도 실행하지 않고 계획과 예상 비용만 출력합니다(작업 폴더도 만들지 않음). 실제 CLI를 띄우는 곳은 `live_exec` 하나이고, `main()`이 아래 조건을 모두 확인한 뒤에만 켭니다. `run`·`loop`·`grade`·`calibrate`의 `--live`에 같은 조건이 걸립니다: ① 유한한 `--budget-usd > 0`, ② 평가셋 검증 문제 0개, ③ `requirements`·`eval_set` 승인, ④ 모든 모델의 가격을 앎(`--price`는 유한한 양수만, `--tokens`도 양수만), ⑤ (run·loop) preflight 문제 0개, ⑥ (run·loop) Codex의 호출당 최악 비용 상한이 예산 안, ⑦ 예상 비용 ≤ 예산. **`loop`는 매 iteration 시작 전에 ①~⑦을 다시 확인합니다**(평가셋을 다시 읽어 검증, `eval_set` 승인 해시 — 입력 파일 포함 — 재확인, preflight 재실행, 남은 iteration 비용 ≤ 남은 예산). 하나라도 실패하면 `gates-failed`로 멈춥니다.
- **builder**: 셸 명령 `--builder`는 없앴습니다(지정하면 dry-run이든 `--live`든 오류). 임의 셸 명령은 유료 CLI를 예산 밖에서 부를 수 있기 때문입니다. 대신 `--builder-prompt FILE`은 iteration 사이에 `claude -p` 한 번을 `live_exec`로 실행합니다: 작업 폴더 = 스킬 폴더, `--tools Read,Edit,Write,Glob,Grep`(Bash 없음 → 다른 CLI를 띄울 수 없음), `--permission-mode acceptEdits`, `--disable-slash-commands`, `--max-budget-usd`(남은 예산의 절반), 비용은 같은 예산에서 차감(보고가 없으면 상한액 차감). 직전 iteration의 `comparison.md`를 프롬프트에 넣습니다. `--builder-model`(기본 `claude-opus-5-5`)과 `--tokens builder=IN,OUT`(가정 80k/8k)로 `loop` 예상 비용에 (iteration 수 − 1)회가 더해집니다. builder가 `evals/`를 고치면 다음 iteration 전 승인 재확인에서 멈춥니다. 편집 범위가 스킬 폴더로 한정되는지는 확인하지 않았습니다(추정: acceptEdits는 작업 폴더 기준).
- **예상 비용의 근거**: `--live`일 때는 항상 검증된 평가셋 개수로 계산합니다. `--cases/--llm-cases/--trigger-queries`는 `estimate`·`plan` 전용이고 다른 명령에 주면 오류입니다. `grade --live`는 그 iteration 폴더에 실제로 있는 run 수(LLM 항목 있는 run + pairwise 짝)로, `calibrate --live`는 골든 예시 개수 × 채점 호출로, `loop --live`는 한 iteration 비용(+`--golden` 보정) × `--max-iterations`로 계산합니다.
- **호출당 예산 제어(H)**: 실행 중 실제 사용액(Claude는 `total_cost_usd`, 아니면 토큰×가격, 둘 다 없으면 그 호출의 상한)을 예산에서 빼고, 예산이 모자라면 멈추고 `plan.json`의 `stopped`에 적습니다. Claude(실행·채점 모두)에는 남은 예산의 절반(`CLAUDE_CAP_HEADROOM` 0.5, 센트 내림)만 `--max-budget-usd`로 넘깁니다. CLI가 이 상한을 얼마나 넘을 수 있는지는 문서에서 확인하지 못해 여유를 둔 값입니다. 그 값이 $0.01 미만이면 호출하지 않습니다. **Codex는 호출당 상한 옵션이 없습니다.** 그래서 호출 전마다 최악 비용 상한(가정 토큰 × `WORST_CASE_FACTOR` 3 × 단가)이 남은 예산 안일 때만 호출합니다. 이 값은 추정 상한이지 **강제 상한이 아니며**, 실제 한 호출이 이보다 비쌀 수 있습니다(`--tokens`로 가정을 키우면 더 보수적).
- `--claude-arg`·`--codex-arg`로 모델·예산을 바꾸거나 격리를 여는 플래그는 거부합니다: Claude `--model`, `--fallback-model`, `--max-budget-usd`, `--settings`, `--setting-sources`, `--plugin-dir`, `--plugin-url`, `--add-dir`, `--mcp-config`; Codex `-m`, `--model`, `-p`, `--profile`, `--oss`, `-c/--config model…`, `-C`/`--cd`, `--add-dir`. 가격은 `--claude-model`/`--codex-model` 기준이기 때문입니다.

| 명령 | 하는 일 |
|---|---|
| `validate` | evals.json(항목 layer·check, pressure)과 trigger_evals.json(정확히 10+10) 검사 |
| `estimate` | 예상 호출 수·토큰·비용 표. `--cases/--llm-cases/--trigger-queries`로 가상 개수, `--tokens KIND=IN,OUT`, `--price KEY=IN,OUT` |
| `plan` | 검증 문제, 승인 상태, preflight 문제, 엔진·설정별 예시 명령, 예상 비용 (항상 dry run) |
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
    └── <engine>/                      # claude | codex
        ├── benchmark.json             # 이 엔진만, skill-creator 스키마 그대로
        ├── eval-<id>-<name>/
        │   ├── eval_metadata.json, comparison-<k>.json
        │   └── with_skill|without_skill|old_skill/run-<k>/
        │       ├── workdir/           # 격리 실행 폴더에서 돌려받은 결과물 (설치한 스킬 사본은 제외). 실행 자체는 임시 sandbox에서
        │       ├── outputs/transcript.json, outputs/final.md
        │       ├── timing.json        # total_tokens, duration_ms, input/output/cached tokens, cost_usd
        │       └── grading.json
        └── trigger/<config>/results.json   # run_eval.py 출력 형식
```

**엔진 어댑터** (`Engine.command/parse/triggered`, 테스트에서는 `exec_fn`을 가짜로 바꿈)
- Claude Code: `claude -p <prompt> --output-format stream-json --verbose --no-session-persistence --setting-sources project,local --strict-mcp-config --settings '{"disableBundledSkills": true}' --model M`. 스킬 있음 = sandbox `work/.claude/skills/<name>/`에 복사, 스킬 없음 = 추가로 `--disable-slash-commands`(로컬 `claude --help`: "Disable all skills"). 트리거 = `Skill` 도구 호출의 `skill`이 이름과 같음(agentskills.io 예시 jq와 같은 기준) 또는 `.../skills/<name>/SKILL.md` Read(run_eval.py도 Read를 봄). 권한 모드는 넣지 않습니다. 사용자 settings.json의 권한도 읽지 않으므로(`--setting-sources`에서 `user` 제외), 스킬이 Bash 등을 써야 하면 `--claude-arg=--permission-mode --claude-arg=acceptEdits`, `--claude-arg=--allowedTools --claude-arg=Bash`처럼 직접 줍니다.
- Codex: `codex exec --json --skip-git-repo-check --ephemeral --sandbox workspace-write <prompt>`. 스킬 있음 = sandbox `work/.agents/skills/<name>/`. 환경: `HOME`·`CODEX_HOME`을 sandbox 안으로 바꿈(아래 Eval isolation). 따라서 사용자 `config.toml`(모델 등)도 읽지 않고 CLI 기본값을 씁니다. 토큰은 `turn.completed.usage`(`reasoning_output_tokens`는 비용 상한 쪽으로 출력에 더함 — 과금 규칙은 확인 안 함). `-m`은 `--codex-model`을 줄 때만 붙입니다(공식 페이지에서 확인 못 함). **트리거 감지는 (추정)**: Codex 문서에 스킬 호출 이벤트가 없어, 항목에 `skills/<name>/SKILL.md`가 나오면 트리거로 봅니다. 첫 실제 실행에서 확인 필요.
- preflight와 실행 격리: 아래 **Eval isolation**.

### Eval isolation
목표: 실행마다 깨끗한 환경. with_skill에는 **시험 중인 스킬 하나만**, without_skill에는 **스킬 없음**. 사용자 수준 스킬(`~/.claude/skills`, claude.ai synced, 플러그인, `~/.agents/skills`, `~/.codex/skills`)이 섞이지 않게 하고, **사용자의 기존 로그인은 바꾸지 않습니다.** 확인할 수 없으면 실행하지 않습니다(fail closed).

**공통 (run마다)**
- `--run-root`(기본: 시스템 임시 폴더) 아래에 새 `skill-eval-<engine>-XXXX/`를 만들고 `work/`에 그 케이스의 입력 파일과(with_skill이면) 스킬 사본(`evals/` 제외)만 넣습니다. 실행이 끝나면 `work/`의 결과물(설치한 스킬 사본 제외)을 `run-k/workdir/`로 옮겨 정적 검사에 쓰고 sandbox를 지웁니다(예외가 나도 지움). 실행 폴더가 더는 workspace(스킬 옆) 아래가 아니므로 저장소 안 `.claude/skills`가 상위 폴더로 보이는 문제도 없어졌습니다.
- 실행 직전 검사(sandbox): 프로젝트 스킬 폴더가 정확히 `[<name>]`(with) 또는 `[]`(without)인지, sandbox의 모든 상위 폴더에 `.claude/skills`·`.agents/skills` 스킬이 없는지. 문제가 있으면 CLI를 띄우기 전에 iteration을 멈춥니다(`plan.json` `stopped`).

**Claude Code** — HOME·`CLAUDE_CONFIG_DIR`은 그대로 둡니다.
- 근거(확인): "If you've set the `CLAUDE_CONFIG_DIR` environment variable, Claude Code … keys the macOS Keychain entry to that directory too, so a session with a different `CLAUDE_CONFIG_DIR` reads a different entry." (https://code.claude.com/docs/en/authentication, 2026-10-15 읽음). 임시 HOME/CONFIG_DIR은 로그인을 잃으므로 쓰지 않습니다. `--bare`도 "OAuth and keychain are never read"(로컬 `claude --help`)라 쓰지 않습니다.
- `--setting-sources project,local`: `"user"` 소스가 "`~/.claude/skills/`, `~/.claude/commands/`, and `~/.claude/agents/`"와 사용자 settings.json을 읽는다고 문서에 있습니다(https://code.claude.com/docs/en/agent-sdk/claude-code-features — SDK 옵션 설명. **CLI 플래그가 똑같이 동작하는지는 추정**). 플러그인 활성화(`enabledPlugins`)도 사용자 settings.json에 있으므로 플러그인 스킬도 빠질 것으로 봅니다(추정). claude.ai synced 스킬: skills 페이지가 `user`를 뺀 `--setting-sources`면 동기화하지 않는다고 하지만, 이미 내려받은 `~/.claude/skills/synced/…` 사본을 안 읽는지는 확인 못 함(추정) — 그래서 아래 init 검사로 확인합니다.
- `--strict-mcp-config`("Only use MCP servers from --mcp-config", 로컬 help), 환경변수 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `ENABLE_CLAUDEAI_MCP_SERVERS=false`(SDK 문서 "What settingSources does not control" 표), `--settings '{"disableBundledSkills": true}'`("To turn bundled skills off, use the `disableBundledSkills` setting", https://code.claude.com/docs/en/skills) — 두 설정이 같은 조건이 되도록.
- without_skill: `--disable-slash-commands`(로컬 help "Disable all skills").
- 실행 후 검사(fail closed): stream-json `system/init` 이벤트의 `skills`·`plugins` 목록으로 — 플러그인 0개, with_skill에 시험 스킬이 있음, without_skill에 스킬 0개, 사용자 수준 스킬 이름(`~/.claude/skills` + synced + 플러그인)이 없음. init 이벤트나 `skills` 목록이 없으면 확인 불가로 보고 실패 처리. 이 필드 이름은 Agent SDK 문서 기준 **(추정)**이며 첫 실제 실행에서 확인해야 합니다. 위반하면 그 run을 오류로 기록하고 iteration을 멈춥니다(이미 쓴 비용은 차감).
- preflight: 시험 스킬과 **같은 이름의 사용자 수준 사본**(`~/.claude/skills/**`, synced 포함, 플러그인)이 있으면 거부합니다(`--setting-sources`로 가려지는지 미확인이므로). 사용자 settings.json이 인증을 제공(`apiKeyHelper`, `env`의 `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`/`CLAUDE_CODE_OAUTH_TOKEN`)하면 `user`를 빼는 순간 인증이 사라지므로 거부합니다(값은 읽어 출력하지 않고 키 존재만 봄). 현재 이 PC의 settings.json에는 그런 키가 없습니다(`enabledPlugins`만 있음, 2026-10-15 확인).

**Codex** — run마다 `HOME=<sandbox>/home`, `CODEX_HOME=<sandbox>/codex_home`.
- 스킬 위치(https://learn.chatgpt.com/docs/build-skills, 조사 에이전트 인용): REPO `$CWD/.agents/skills`·상위·`$REPO_ROOT/.agents/skills`, USER `$HOME/.agents/skills`, ADMIN `/etc/codex/skills`, SYSTEM(내장). `$CODEX_HOME/skills`(이 PC의 `~/.codex/skills`에 사용자 스킬 11개 + `.system/`)는 공식 문서에 없고 제3자 자료만 있음(추정). 둘 다 sandbox로 바꾸므로 어느 쪽이든 가려집니다. HOME을 바꾸면 `~/.agents/skills`가 가려진다는 것은 문서 표현("$HOME/.agents/skills") 기준 **추정**(코드 미확인).
- 인증: "`file` stores credentials in `auth.json` under `CODEX_HOME`"(https://learn.chatgpt.com/docs/auth). `CODEX_API_KEY`가 있으면 그것을 씁니다("You can use `CODEX_API_KEY` with `codex exec`", non-interactive 문서). 없으면 sandbox `codex_home/auth.json`을 진짜 `$CODEX_HOME/auth.json`의 **심볼릭 링크**로 둡니다 — 비밀 내용을 복사하거나 로그에 쓰지 않고, 정리할 때 `rmtree`는 링크만 지웁니다(테스트로 확인). 위험(추정): Codex가 토큰을 갱신하며 auth.json을 새 파일로 바꿔 쓰면(rename) 갱신된 토큰은 sandbox에만 남고 진짜 auth.json이 낡을 수 있습니다. 그래서 실행 후 링크가 일반 파일로 바뀌었으면 iteration을 멈추고 **그 sandbox를 지우지 않고 남깁니다**(경로 안내, `codex login status` 확인 권고). keyring 저장 방식이면 링크할 파일이 없어 preflight가 거부합니다(`CODEX_API_KEY` 필요).
- sandbox에 `config.toml`이 없으므로 사용자 Codex 설정(모델, MCP 등)은 쓰지 않습니다. 모델은 `--codex-model`(없으면 CLI 기본값).
- 실행 직전 검사: sandbox HOME의 `.agents/skills`, sandbox CODEX_HOME의 `skills`(`.system/` 내장 스킬 제외), `/etc/codex/skills`에 스킬이 하나라도 있으면 거부. Codex는 로드된 스킬 목록을 보고하는 이벤트가 문서에 없어 실행 후 검사는 auth 링크만 합니다(추정 한계).
- 확인 못 한 것: 이 세션에서 `codex exec --help` 실행이 허용되지 않아 Codex 플래그는 문서로만 확인했습니다. `.system` 스킬이 새 CODEX_HOME에 자동 설치되는지(제3자 자료는 그렇다고 함).

**preflight**(`run`·`loop --live`를 막음, 읽기 전용): run-root와 그 모든 상위 폴더의 `.claude/skills`·`.agents/skills`에 스킬이 있으면 거부, Claude 같은 이름 사용자 사본, Claude 인증이 사용자 settings에서 오는 경우, `/etc/codex/skills`의 스킬, Codex 인증 없음(`CODEX_API_KEY`도 file 방식 auth.json도 없음). 사용자 수준 Codex 스킬(`~/.agents/skills`, `~/.codex/skills`)은 sandbox HOME/CODEX_HOME으로 가려지므로 더는 거부 사유가 아닙니다. 끊어진 심볼릭 링크와 링크 순환은 건너뜁니다(테스트로 확인).

**참고한 표준 방식**: skill-creator `scripts/run_eval.py`는 격리를 거의 하지 않습니다 — `.claude`가 있는 프로젝트 루트를 cwd로 쓰고 `.claude/commands/<name>.md`를 잠깐 만들었다 지우며, 환경에서 `CLAUDECODE`만 뺍니다(https://github.com/anthropics/skills/blob/main/skills/skill-creator/scripts/run_eval.py, 조사 에이전트 확인). agentskills.io는 "Each eval run should start with a clean context — no leftover state from previous runs or from the skill development process."라고만 합니다(https://agentskills.io/skill-creation/evaluating-skills). 이 하네스의 sandbox·사용자 스킬 차단·init 검사는 그보다 엄격한 자체 규칙(H)입니다.

**채점**
- 순서: static(정규식·도구 호출·트리거·파일) → llm → human. LLM 채점자는 그 iteration의 `plan.json`에 기록된 실행 모델과 **다른 모델**이어야 합니다(같으면 오류, Claude 실행 모델을 모르면 오류, `plan.json`이 없으면 오류 — `grade` 명령의 `--claude-model`은 보지 않음). 기본 실행 `claude-opus-5-5`, 채점 `claude-sonnet-5-5`. 채점은 `claude -p --json-schema ... --tools ""`로 도구 없이 yes/no와 근거를 받습니다.
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

**첫 실제 eval 대상 (제안)**: 이전 계획의 `dev-setup`은 pinesky-skills 저장소와 함께 삭제되어 대상에서 뺐습니다. 대신 이 저장소의 `golden/skills/csv-cleaner`를 제안합니다. 이유: 저장소 안에 있어 사라질 위험이 없고, 같은 이름의 설치본이 없으며(preflight 문제 0, 2026-10-15 dry-run), `scripts/clean.py`가 결정적이라 결과를 기계로 확인하기 쉽고, 입력 파일(`evals/files/*.csv`)이 이미 있습니다. 실행 전에 할 일: ① `evals/trigger_evals.json`(10+10) 작성, ② 문자열 assertion 일부를 `static` 검사 객체로(예: `file_exists` `orders_clean.csv`), ③ eval 1 프롬프트의 `data/orders_2025.csv` 경로가 실제 입력 위치 `evals/files/orders_2025.csv`와 다름 — 맞출지 결정, ④ `requirements.md` 작성과 `requirements`·`eval_set` 승인, ⑤ 첫 실행은 `--engines claude --runs 1`로 init 이벤트 필드(격리 검사) 확인. `style-lab`은 `disable-model-invocation`이라 트리거 평가가 의미가 없고, `find-skills`는 `~/.agents/skills`에 원본만 있어(저장소 없음) 후순위입니다.

**비용 추정**: 호출 수 = 실행(케이스 × 설정 2 × runs) + 트리거(쿼리 × runs × 스킬 있는 설정 수) + 채점(llm 항목 있는 케이스 × 설정 2 × runs) + pairwise(케이스 × runs), 엔진마다. 호출당 토큰은 **가정값**입니다(exec 60k/4k, trigger 25k/500, grader 12k/800, pairwise 16k/800 — 측정값 아님, 파일럿 뒤 `--tokens`로 교체). 입력 토큰은 전부 기본 입력 단가로 계산해 캐시 할인을 빼므로 상한 쪽 추정입니다. Claude 단가는 위 요금 페이지(2026-10-15)에서 옮긴 값이고, Codex/OpenAI 단가는 1차 출처로 확인하지 못해 넣지 않았습니다(`--price codex=IN,OUT` 필요, 없으면 `--live` 거부).

### skill-creator 대비 차이(deviation)
- **엔진 계층 추가**: skill-creator는 `iteration-N/eval-*/<config>/run-*`입니다. 하네스는 엔진이 둘이라 `iteration-N/<engine>/eval-*/...`로 한 단계 더 둡니다. 엔진 폴더 안은 skill-creator 구조 그대로이고 엔진별 `benchmark.json`도 skill-creator 스키마입니다. skill-creator `scripts/aggregate_benchmark.py iteration-N/<engine>`은 모든 run에 채점된 항목이 하나 이상 있을 때 동작합니다(가짜 엔진 결과로 확인). 아직 채점 안 된 run(`pass_rate: null`, 예: human 항목만 있는 케이스의 리뷰 전)이 있으면 그 스크립트는 `TypeError`로 멈추므로 하네스의 `report`를 쓰세요. 최상위 `iteration-N/benchmark.json`은 같은 필드에 `per_engine`, `trigger`, `pairwise`, `static_failures`, `pressure`, `human_pending`, `llm_not_graded`, `calibration`, `gate`를 더하고 `runs[]`에 `engine`을 더한 확장입니다.
- **eval 폴더 이름**: skill-creator `eval-<ID>`/설명 이름, agentskills.io `eval-<설명>`. 하네스는 `eval-<id>-<name>`(name 없으면 프롬프트 slug)이고 eval_id는 `eval_metadata.json`에 둡니다(aggregate_benchmark가 읽는 위치).
- **grading.json**: `expectations[]`의 `text`/`passed`/`evidence`는 같고 `id`, `layer`, `status`, `grader`를 더합니다. `passed`는 미채점(human 대기, dry-run llm)일 때 `null`이고 `summary.pass_rate` 분모에서 빠지며 `summary.pending`에 셉니다(skill-creator는 bool만). `claims`, `user_notes_summary`, `eval_feedback`는 만들지 않습니다. agentskills.io 예시는 `assertion_results`라는 이름을 쓰지만 skill-creator 뷰어가 `expectations`를 읽으므로 그쪽을 따랐습니다.
- **benchmark.json delta**: skill-creator 형식(문자열 `"+0.50"`)을 따릅니다. agentskills.io 예시는 숫자(`0.50`)입니다. 실행 결과에 `pending`, `cost_usd`를 더하고, 채점 항목이 0개인 run은 pass_rate 통계에서 뺍니다(skill-creator는 0.0으로 넣음).
- **timing.json**: `total_tokens`, `duration_ms`, `total_duration_seconds`, `executor_start/end`는 같고 `input_tokens`, `output_tokens`, `cached_input_tokens`, `cost_usd`를 더합니다. 시간은 하네스가 CLI 프로세스를 감싸 잰 벽시계 시간이고, 토큰은 CLI 출력에서 읽습니다(subagent 알림이 아님). 채점자 시간은 넣지 않습니다.
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
