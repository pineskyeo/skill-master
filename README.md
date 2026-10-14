# 에바 (Eva) — 스킬·에이전트 평가기

스킬과 에이전트를 업계 표준 구조(Anthropic Skills / agentskills.io / Hermes)로 검증하고, 실패 이유와 고칠 방법을 빌더 에이전트에게 돌려줍니다. Python 3.9 표준 라이브러리만 씁니다.

## 사용법
```
python3 eva.py check golden/skills/pdf-summarizer        # 하나 검사 (PASS면 종료코드 0)
python3 eva.py check <path> --json                      # JSON 출력
python3 eva.py check-all golden                         # 폴더 전체
python3 eva.py feedback <path> --round 1                # FEEDBACK.md / FEEDBACK.json 작성
python3 eva.py check <agent> --transcript run.json      # 실행 기록 채점
python3 eva.py index golden                             # INDEX.md / registry.json 생성
python3 loop.py <path> --builder "<cmd>"                # 자동 피드백 루프 (LOOP.md)
python3 -m unittest discover -s tests -v                # 테스트
```

## 구조
- 스킬: `<name>/SKILL.md` (frontmatter: name, description, 선택 version·inputs·outputs) + 선택 `scripts/`, `references/`, `assets/`
- 에이전트: `<name>/AGENT.md` (frontmatter: name, description, goal, tools, success_criteria) + `scenarios/*.json`
  - 시나리오: `{"id", "turns":[...], "expected_tools":[...], "must_include":[...], "expected_outcome"}`
- `golden/` 좋은 예시(스킬 3, 에이전트 2), `bad/` 일부러 틀린 예시, `transcripts/` 실행 기록 예시

## 평가 기준
**스킬**
- 단일 작업: name kebab-case(64자 이하), description 존재·20~1024자·"언제 쓰는지" 포함, 여러 일 나열 금지
- 명확한 입출력: inputs, outputs 선언, `## Example`
- 구조: frontmatter, name = 폴더명, SKILL.md 500줄·20KB 이하, 참조한 scripts/references/assets 파일 존재, version은 semver

**에이전트**
- 목표 달성: goal, 측정 가능한 success_criteria, 시나리오 2개 이상·각각 expected_outcome
- 도구 사용: tools 선언, 시나리오 expected_tools ⊆ tools, `## Tools` 설명
- 맥락 유지: `## Memory & Handoff` 섹션, 2턴 이상 시나리오

**판정**: 통과율 80% 이상이고 [필수] 항목(frontmatter, name-format, name-matches-folder, description-present, goal-present, tools-declared)을 모두 통과하면 PASS.
