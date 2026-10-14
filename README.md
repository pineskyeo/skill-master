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
python3 -m unittest discover -s tests -v                # 테스트
```

## 스킬 구조 (agentskills.io 명세)
```
<name>/
├── SKILL.md            # 필수: frontmatter + 본문
├── scripts/            # 선택: 실행 코드
├── references/         # 선택: 필요할 때 읽는 문서
├── assets/             # 선택: 템플릿, 데이터
└── evals/evals.json    # 에바 필수: skill-creator 형식 평가셋
```
- frontmatter 필드는 `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`만 쓸 수 있습니다. 그 밖의 필드(예: 예전의 `inputs:`/`outputs:`)는 `frontmatter-fields` [필수] 실패입니다. 입출력은 본문 `## Inputs`/`## Outputs`에 씁니다.
- frontmatter는 YAML이어야 합니다. 첫 줄과 닫는 줄은 정확히 `---`입니다(H: skills-ref는 `---`로 시작하고 다음 `---`까지를 읽음). 그 사이는 `key: value` 매핑이고, 키는 중복될 수 없습니다. 들여쓰기에는 공백만 씁니다(탭 금지). 값에 `: `가 들어가거나 값이 YAML 기호(`&`, `*`, `!`, `{`, `@` 등)로 시작하면 YAML 오류이므로 따옴표로 감쌉니다. 공백 뒤 `#`은 YAML 주석이라 값이 거기서 끝납니다(오류 아님, YAML과 동일).
- metadata 값은 따옴표를 강제하지 않습니다. skills-ref는 strictyaml로 읽어 `version: 1.0`, `beta: yes` 같은 값도 문자열로 받습니다. 명세 예시처럼 `"1.0"`으로 감싸는 것을 권장하지만 검사하지는 않습니다.
- `evals/evals.json` (skill-creator / agentskills.io 형식):
  ```json
  {"skill_name": "<frontmatter name>",
   "evals": [{"id": 1, "prompt": "실제 사용자 요청", "expected_output": "성공의 모습",
              "files": ["evals/files/x.csv"], "assertions": ["확인 가능한 문장"]}]}
  ```
  `files`, `assertions`는 선택입니다. agentskills.io 문서는 `assertions`, skill-creator `schemas.md`는 `expectations`라고 써서 두 이름을 모두 받습니다.

## 스킬 평가 기준
[필수]는 점수와 상관없이 하나라도 실패하면 FAIL입니다. 출처 표시: **S**=agentskills.io 명세, **V**=skills-ref / quick_validate 검증기, **B**=Anthropic 모범 사례, **C**=skill-creator / agentskills.io 평가 문서, **H**=자체 규칙(명세보다 엄격함).

| id | 내용 | 출처 |
|---|---|---|
| `frontmatter` [필수] | `---`로 감싼 YAML 매핑 | S |
| `frontmatter-fields` [필수] | 명세의 6개 필드만 허용 | S, V |
| `field-types` [필수] | name·description·compatibility·license·allowed-tools는 문자열, metadata는 문자열→문자열 매핑(목록·중첩 매핑 불가) (S). name·description·compatibility 값이 PyYAML에서 숫자·불리언·null·날짜로 읽히면(예: `name: 123`) 따옴표 필요 (V: quick_validate가 `yaml.safe_load` 후 `str`인지 검사). license·allowed-tools가 비어 있으면 실패 (H). metadata 값은 따옴표 강제 안 함 | S, V, H |
| `name-format` [필수] | skills-ref `_validate_name`과 같음: NFKC 정규화 후 1~64자, 소문자(`name == name.lower()`), 유니코드 글자·숫자(`str.isalnum()`)와 `-`만, `-`로 시작·끝나지 않음, `--` 없음. 참고: skill-creator quick_validate는 ASCII `^[a-z0-9-]+$`만 허용하므로 `한국어-교정` 같은 이름은 quick_validate에서는 실패합니다 | S, V |
| `name-matches-folder` [필수] | NFKC(폴더 이름) = NFKC(name) | S, V |
| `name-reserved` [필수] | name에 `anthropic`, `claude`가 들어가면 안 됨 | B |
| `description-present` [필수] | description이 비어 있지 않음 | S |
| `description-no-xml` [필수] | description에 `<`, `>` 없음 | B, V |
| `description-length` | 20~1024자 (명세는 1~1024자, 최소 20자는 자체 규칙) | S, H |
| `description-when` | 언제 쓰는지 적음 ("Use when", "할 때" 등) | S, B |
| `single-purpose` | 여러 일을 나열하지 않음 | H |
| `compatibility-length` [있으면 필수] | 1~500자 | S, V |
| `inputs-declared`, `outputs-declared`, `example-present` | 본문 `## Inputs`, `## Outputs`, `## Example` 제목 (명세는 예시 섹션을 권장). 제목은 CommonMark ATX 규칙: 앞 공백 0~3칸, `#` 1~6개 뒤 공백·탭(또는 줄 끝). 코드 블록 안 제목은 무시 | S(권장), H |
| `size-limit` | SKILL.md 500줄 미만, 20KB 이하 (20KB는 "본문 5000토큰 미만" 권장치를 대신하는 자체 기준) | S, B, H |
| `refs-relative` | 참조는 스킬 폴더 기준 상대경로, 슬래시(`/`) 사용, 폴더 밖(`..`)이나 절대경로 금지 | S, B |
| `refs-exist` | 참조한 파일이 실제로 있음. 참조 찾기: 코드 블록(```` ``` ````, `~~~`)을 먼저 빼고, ① 마크다운 링크의 상대경로, ② 인라인 코드나 일반 문장 속 `scripts/…`, `references/…`, `assets/…` 경로(예: "Run scripts/x.py")를 찾음. URL, `#앵커`, 폴더 언급(`scripts/`), 글로브(`assets/*.png`), `my-scripts/x`처럼 다른 단어에 붙은 경로는 참조가 아님 | S |
| `refs-one-level` | SKILL.md가 링크한 파일이 다시 다른 파일을 링크하지 않음(참조 사슬 금지) | S, B |
| `refs-path-depth` | 참조 경로는 폴더 한 단계까지(`references/x.md`). 명세에는 없는 자체 규칙 | H |
| `reference-toc` [해당 시] | SKILL.md가 링크한 100줄 넘는 .md 파일은 제목 바로 다음에 목차(`## Contents`/`## 목차`) | B |
| `version-format` [있으면 필수] | `metadata.version`은 SemVer `x.y.z`. 자체 규칙이며, 명세 예시의 `"1.0"`은 여기서 실패함 | H |
| `evals-valid` [필수] | `evals/evals.json`이 있고 위 형식을 따름: `skill_name` = name, `id` 정수 중복 없음, `prompt`·`expected_output` 문자열, `files`는 스킬 안에 실제로 있음, `assertions`/`expectations`는 문자열 목록 | C |
| `evals-count` [필수] | 평가 케이스 3개 이상 | B ("At least three evaluations created"), C ("Start with 2-3 test cases") |

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
- **본문 작성 품질** (B): 3인칭 description, 시점에 따라 틀려질 정보 없음, 용어 일관성, 스크립트 오류 처리·상수 근거, MCP 도구 전체 이름(`Server:tool`) — 자동 검사 없음
- **description 시점 규칙 충돌**: Anthropic 모범 사례는 "3인칭으로 쓰라"고 하고, agentskills.io description 최적화 문서는 "Use this skill when..." 같은 명령형을 권합니다. 어느 쪽을 검사할지는 정하지 않았습니다(확인 필요).

## [분리 예정] 에이전트 평가
`agent master`로 옮길 때까지 동작은 그대로입니다.
- 에이전트: `<name>/AGENT.md` (frontmatter: name, description, goal, tools, success_criteria) + `scenarios/*.json`
  - 시나리오: `{"id": str, "turns": [{"user": str}, ...], "expected_tools": [str, ...], "must_include": [str, ...], "expected_outcome": str | [str, ...], "success_criteria"(선택): str | [str, ...]}`. 형식이 틀리면 `scenarios-valid` [필수] 실패
- 정적 검사: goal, 측정 가능한 success_criteria, 시나리오 2개 이상·expected_outcome, tools 선언과 expected_tools ⊆ tools, `## Tools`, `## Memory & Handoff`, 2턴 이상 시나리오
- `success-measurable` [필수]: success_criteria 중 자동 확인 가능한 것이 1개 이상. 자동 확인이 안 되는 기준(예: "모든 지적에 파일:줄 근거를 단다", "모든 주장에 출처를 단다")은 지우지 않고 그대로 두며, transcript 채점 때 needs-review로 나옵니다.
- transcript 채점([필수]): `transcript-valid`, `agent-valid`, `scenario-known`, `only-declared-tools`, `expected-tools-called`, `outcome-keywords`, `no-answer-lookup`, 결과 검증(검증된 출처 URL 개수, 파일:줄 근거 개수, 파일 변경 0건, 글자 수, 비교표). 확인 가능한 조건이 하나도 없으면 `outcome-evidence` 실패. 형식은 LOOP.md 참고
- 파일:줄 근거: 최종 답의 `경로:줄`(또는 `경로:시작-끝`)은 **그 줄이 도구 결과에 줄 번호와 함께 나왔을 때만** 셉니다. 인정하는 결과 형식: grep/rg `-n` 형식 `경로:줄:내용`(문맥 줄 `경로-줄-내용`, 어느 결과에 있어도 됨), 또는 `cat -n`/Read 도구 형식 `줄<탭|→|:|공백>내용`(그 호출의 `input`에 적힌 파일에만 해당, 호출에 붙은 `result`일 때만). 파일 이름만 나오거나 없는 줄(예: `:99999`)은 세지 않습니다.
- 예시: `golden/agents`(2), `bad/agents`, `transcripts/`. 자동 검사를 모두 통과하고 needs-review가 남는 REVIEW 예시는 `research-followup-cited.json`, `code-reviewer-drill.json`. FAIL 예시는 `research-followup.json`, `bad/research-fabricated-urls.json`, `bad/code-reviewer-answer-lookup.json`

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
