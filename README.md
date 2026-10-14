# 에바 (Eva) — 스킬·에이전트 평가기

스킬과 에이전트를 업계 표준 구조(Anthropic Skills / agentskills.io / Hermes)로 검증하고, 실패 이유와 고칠 방법을 빌더 에이전트에게 돌려줍니다. Python 3.9 표준 라이브러리만 씁니다.

## 사용법
```
python3 eva.py check golden/skills/pdf-summarizer        # 하나 검사 (PASS면 종료코드 0)
python3 eva.py check <path> --json                      # JSON 출력
python3 eva.py check-all golden                         # 폴더 전체 (하나라도 FAIL이면 종료코드 1)
python3 eva.py feedback <path> --round 1                # FEEDBACK.md / FEEDBACK.json 작성
python3 eva.py check <agent> --transcript run.json      # 실행 기록 채점
python3 eva.py index golden                             # INDEX.md / registry.json 생성
python3 loop.py <path> --builder "<cmd>"                # 자동 피드백 루프 (LOOP.md, 빌더 실패 시 종료코드 2)
python3 -m unittest discover -s tests -v                # 테스트
```

## 구조
- 스킬: `<name>/SKILL.md` (frontmatter: name, description, 선택 license·compatibility·allowed-tools·metadata.version(SemVer)·inputs·outputs) + 선택 `scripts/`, `references/`, `assets/`, `evals/evals.json`(skill-creator 방식)
- 에이전트: `<name>/AGENT.md` (frontmatter: name, description, goal, tools, success_criteria) + `scenarios/*.json`
  - 시나리오: `{"id", "turns":[...], "expected_tools":[...], "must_include":[...], "expected_outcome", "success_criteria"(선택)}`
- `golden/` 좋은 예시(스킬 3, 에이전트 2), `bad/` 일부러 틀린 예시, `transcripts/` 실행 기록 예시
  (`research-followup.json`은 출처·표가 없어 FAIL, `research-followup-cited.json`은 PASS)

## 평가 기준
**스킬**
- 단일 작업: name kebab-case(64자 이하), description 존재·20~1024자·"언제 쓰는지" 포함, 여러 일 나열 금지
- 명확한 입출력: inputs, outputs 선언, `## Example`
- 구조: frontmatter, name = 폴더명, SKILL.md 500줄·20KB 이하, 참조 파일 존재·한 단계 깊이, metadata.version은 SemVer, evals.json 형식

**에이전트**
- 목표 달성: goal, 측정 가능한 success_criteria, 시나리오 2개 이상·각각 expected_outcome
- 도구 사용: tools 선언, 시나리오 expected_tools ⊆ tools, `## Tools` 설명
- 맥락 유지: `## Memory & Handoff` 섹션, 2턴 이상 시나리오

**실행 기록(transcript) 채점**
- 선언 안 된 도구 사용, 기대 도구 누락, `must_include` 키워드 누락
- 결과 검증: 에이전트 `success_criteria` + 시나리오 `success_criteria`·`expected_outcome`의 각 조건을 최종 답(`final`)에서 확인 가능한 증거로 채점 (모두 [필수])
  - "출처/인용/링크/source N개 이상" → 서로 다른 URL 개수 ≥ N
  - "N자 이하/이내" → 최종 답 길이 ≤ N
  - "비교표/표로/table" → 헤더+구분선(`|---|`)이 있는 마크다운 표
  - 그 밖의 조건은 자동 확인 불가 → N/A. 확인 가능한 조건이 하나도 없으면 `outcome-evidence` 실패

**판정**: 아래를 모두 만족하면 PASS.
1. 통과율(N/A 제외) 80% 이상 (`PASS_THRESHOLD`)
2. [필수] 항목 전부 통과: frontmatter, name-format, name-matches-folder, description-present, goal-present, tools-declared, 그리고 transcript 결과 검증 항목
3. 선택 항목(version-format, evals-valid): 없으면 N/A(점수 분모에서 제외), 있으면 반드시 통과해야 함
4. 카테고리 하한: N/A가 아닌 검사가 있는 카테고리마다 1개 이상 통과하고 통과율이 `CATEGORY_FLOOR`(50%) 이상
