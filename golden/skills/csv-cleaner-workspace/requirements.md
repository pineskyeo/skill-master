# csv-cleaner 요구사항

## 하는 일
- CSV 파일 하나를 받아 새 CSV 파일로 정리한다. 원본 파일은 바꾸지 않는다.
- 모든 칸의 앞뒤 공백을 없앤다 (scripts/clean.py: `c.strip()`).
- 공백을 없앤 뒤 완전히 같은 행은 처음 나온 것 하나만 남긴다. 행 순서는 유지한다. 대소문자는 구분한다
  (kim@example.com 과 KIM@example.com 은 다른 행).
- 헤더만 있고 데이터 행이 없는 파일도 오류 없이 헤더만 있는 파일을 만든다.

## 써야 할 때 / 쓰지 말아야 할 때
- 사용: CSV의 중복 행 제거, 칸 앞뒤 공백 정리, 분석 전 이 두 가지 정리.
- 비사용: 집계, 형식 변환, 파일 비교·병합, 값 형식 바꾸기, 결측치 채우기, 키 기준 병합, CSV 아닌 파일, 시각화, 일반 코드 작성.

## 첫 실제 실행 설정
- 시험 대상: Claude Code, claude-sonnet-5-5, --effort high, runs 1, with_skill vs without_skill.
- 채점·블라인드 pairwise: Codex(CLI 기본 모델), reasoning effort high, `--sandbox read-only`. Codex는 시험 대상이 아님.

## 통과 기준 (하네스 게이트)
- with_skill 평균 pass_rate가 without_skill보다 낮지 않다 (회귀 없음).
- with_skill 트리거 평가: should-use 10개 trigger_rate ≥ 0.5, should-not 10개 < 0.5.
- with_skill run의 static 검사 실패가 없다.
- 실행 오류·격리 위반·미채점 항목이 없다.
