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

## 사용법
```
python3 eva.py check golden/skills/pdf-summarizer        # 하나 검사 (종료코드: PASS 0, FAIL 1, REVIEW 3)
python3 eva.py check <path> --json                      # JSON 출력
python3 eva.py check-all golden                         # 폴더 전체 (하나라도 FAIL이거나 검사 대상이 0개면 1, FAIL 없이 REVIEW가 있으면 3)
python3 eva.py feedback <path> --round 1                # FEEDBACK.md / FEEDBACK.json 작성
python3 eva.py index golden                             # INDEX.md / registry.json 생성
python3 loop.py <path> --builder "<cmd>"                # 자동 피드백 루프 (LOOP.md, 빌더 실패 시 종료코드 2, REVIEW면 3)
python3 eva.py check <agent> --transcript run.json      # [분리 예정] 에이전트 실행 기록 채점
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
  `files`, `assertions`, `layer`는 선택입니다. `layer`는 그 케이스를 채점하는 방식이며 값은 정확히 소문자 `static`·`llm`·`human` 중 하나여야 합니다(그 밖의 값·대문자는 `evals-valid` 실패, H). 세 layer가 모두 있을 필요는 없습니다. agentskills.io 문서는 `assertions`, skill-creator `schemas.md`는 `expectations`라고 써서 두 이름을 모두 받습니다.

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
| `refs-exist` | 참조한 파일이 실제로 있음. 스킬 폴더에는 없지만 스킬을 감싼 git 리포 루트 기준으로는 있는 파일(예: pinesky-skills의 `scripts/check_skills.py`)은 실패도 통과도 아닌 **needs-review**("repo-root-relative reference, not bundled")로 보고해 판정이 REVIEW가 됩니다. 리포 루트는 스킬 폴더 realpath에서 위로 올라가며 `.git`(폴더 또는 워크트리 파일)이 있는 첫 폴더이고, `..`·절대경로·역슬래시 참조와 심볼릭 링크를 풀었을 때 리포 루트 밖으로 나가는 경로는 인정하지 않습니다. 진짜 없는 참조가 하나라도 있으면 실패입니다. 절대경로(`/etc/hosts`, `/Users/…`, `~/…`, `C:/…`)·역슬래시 경로, 그리고 `..` 정규화나 심볼릭 링크를 풀었을 때 스킬 폴더와 리포 루트 밖으로 나가는 참조는 대상 파일이 있어도 **필수 실패**(판정 FAIL)입니다. `..`·심볼릭 링크로 스킬 폴더 밖이지만 리포 안의 파일을 가리키면 needs-review, 스킬 폴더 안의 파일(번들)은 통과입니다. 명세는 참조를 상대경로로 쓰라고만 하고 파일 존재 검사는 정하지 않으므로 자체 규칙. 참조 찾기: ① 코드 블록 밖 마크다운 링크의 상대경로, ② 코드 블록 밖 인라인 코드·`**굵게**`·일반 문장 속 `scripts/…`, `references/…`, `assets/…` 경로(예: "Run scripts/x.py", "`scripts/clean.py`를 실행" — 경로 끝에 붙은 한국어 조사와 `**`는 떼어 냄), ③ 코드 블록(```` ``` ````, `~~~`) 안에서는 실행 명령만, 한 줄을 `&&`, `||`, `;`, `|`로 나눈 **모든 명령**에서: 인터프리터(`python`, `python3`, `bash`, `sh`, `zsh`, `node`, `deno run`, `ruby`, `perl`, `Rscript`, `php`, `pwsh`, `uv run`, 앞에 `VAR=값` 허용) 뒤의 번들 경로, 또는 명령 맨 앞 `./scripts/…`·`scripts/…` 직접 실행. 단 `./` 없는 `scripts/…`는 들여쓴 줄, `# 설명`만 붙은 줄(`scripts/extract.py   # helper`), 트리 문자(`├ └ │`)가 있는 블록에서는 파일 목록으로 보고 참조가 아님. `#` 뒤 셸 주석은 보지 않음. URL, `#앵커`, 폴더 언급(`scripts/`), 글로브(`assets/*.png`, `assets/icon*` — 끝의 `*`는 앞에 같은 수의 `*`로 연 강조일 때만 떼어 냄), `my-scripts/x`처럼 다른 단어에 붙은 경로, 코드 블록 안의 실행 명령이 아닌 줄은 참조가 아님. `__밑줄 강조__`로 감싼 경로는 아직 인식하지 않음 | H |
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
아래는 조사한 문서에 있지만 에바가 아직 하지 않는 것입니다. 에바는 정적 검사만 하고, 모델을 실행하지 않습니다.
- **스킬 있음 vs 없음 벤치마크** (C): 같은 프롬프트를 `with_skill`과 `without_skill`(또는 `old_skill`)로 실행하고 `iteration-N/` 작업 폴더에 결과를 남기는 기능
- **채점·집계** (C): assertion별 `grading.json`(`text`/`passed`/`evidence`), `timing.json`(`total_tokens`, `duration_ms`), `benchmark.json`(pass_rate·time·tokens의 mean±stddev와 delta)
- **트리거(description) 평가** (C): should-trigger / should-not-trigger 쿼리 약 20개(각 8~10개, 비슷하지만 다른 일을 하는 근접 오답 포함), 쿼리마다 3회 실행해 트리거 비율 계산(기준 0.5), train 60% / validation 40% 분할, 최대 5회 반복, validation 점수로 최선의 description 선택. 쿼리 파일(`[{"query", "should_trigger"}]`)의 위치는 명세에 없어 정적 검사도 하지 않습니다.
- **assertion 품질 분석** (C): 두 설정 모두에서 늘 통과하거나 늘 실패하는 assertion, 실행마다 결과가 크게 다른 eval 찾기
- **블라인드 비교** (C): 두 버전 출력을 어느 쪽인지 숨기고 LLM 판정자에게 비교시키기
- **사람 리뷰 기록** (C): `feedback.json`과 리뷰 뷰어
- **개선 반복 루프** (C): 실패한 assertion, 사람 피드백, 실행 기록을 LLM에 주고 SKILL.md 수정안을 받는 루프. 지금 `loop.py`는 정적 검사 피드백만 돌립니다.
- **토큰 예산** (S): 메타데이터 약 100토큰, 본문 5000토큰 미만 권장. 토크나이저가 없어 바이트(20KB)로 대신합니다.
- **모델별 테스트** (B): Haiku / Sonnet / Opus 각각으로 확인하기
- **본문 작성 품질** (B): 시점에 따라 틀려질 정보 없음, 용어 일관성, 스크립트 오류 처리·상수 근거, MCP 도구 전체 이름(`Server:tool`) — 자동 검사 없음
- **description 시점**: 동사 형태(3인칭 "Processes…" / 명령형 "Use this skill when…")는 강제하지 않고 1·2인칭 대명사만 `description-pov`로 막습니다. 따옴표 안 인용은 검사하지 않으므로, 인용 안에 숨긴 1·2인칭은 찾지 못합니다. 주어를 생략한 한국어 1·2인칭(예: "도와드릴게요")은 찾지 못합니다.

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
