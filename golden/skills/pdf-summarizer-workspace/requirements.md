# pdf-summarizer 요구사항

## 하는 일
- PDF 파일 하나(pdf_path)를 받아 한 장짜리 한국어 요약 summary.md를 만든다.
- `pdftotext`로 글자를 뽑는다. 이 도구가 없을 때의 동작은 열린 질문의 pdftotext 참고.
- PDF의 언어와 상관없이 요약은 한국어로 쓴다.
- 형식은 references/format.md를 따른다: 제목, 핵심 3줄, 한 줄 결론. 줄 수 한도는 열린 질문의 핵심 줄 수 참고.
- 스캔 이미지뿐이라 pdftotext 출력이 비면 글자를 뽑지 못했다고 알리고 요약을 지어내지 않는다 (evals.json 3번 사례).

## 써야 할 때 / 쓰지 말아야 할 때
- 사용: PDF 하나를 주고 요약을 요청할 때. 한 장 요약, 한국어 요약, 핵심 정리, 짧은 요약, 영어 PDF의 한국어 요약.
- 비사용: docx·txt 파일 요약, 웹 기사 요약, PDF 페이지별 번역, PDF 표를 CSV로 뽑기, PDF 내용 고치기, PDF 병합·분할, 이미지 OCR, 보고서를 써서 PDF로 내보내기.

## 첫 실제 실행 설정
- 시험 사례: evals.json 3개, 평가 항목 5개 (모두 llm 문자열).
- 입력 파일. 어느 사례에도 `files`가 없고 evals/files 폴더도 없다. 샌드박스 작업 폴더에는 사례의 `files`와 with_skill일 때의 스킬만 들어가므로
  (skill_eval.py class Sandbox) 1번 report.pdf, 2번 ~/Downloads/2025_business_plan_v3.pdf, 3번 스캔 PDF는 실행 때 작업 폴더에 없다. 열린 질문의 PDF 입력 참고.
- 트리거 평가: evals/trigger_evals.json의 should-use 10개, should-not 10개.
- 엔진, 모델, effort, runs, 채점자는 스킬 파일에 없다. 열린 질문의 실행 설정 참고.

## 통과 기준 (하네스 게이트)
- with_skill 평균 pass_rate가 without_skill보다 낮지 않다 (회귀 없음). pass_rate는 llm 5개로만 정해진다.
- with_skill 트리거 평가: should-use 10개 trigger_rate ≥ 0.5, should-not 10개 < 0.5.
- with_skill run의 static 검사 실패가 없다. 이 스킬의 static 항목은 0개라 지금은 이 게이트가 검사하는 것이 없고 실패할 수도 없다.
- 실행 오류·격리 위반·미채점 항목이 없다. 평가 항목이 모두 llm이므로 5개 모두 `grade --live` 채점이 끝나야 한다.

## pinesky에게 묻는 열린 질문
- 이 파일은 pinesky 검토용 초안이고, 승인은 기록되어 있지 않다.
- 실행 설정. 스킬 파일에 엔진, 모델, effort, runs, 채점자가 없다. csv-cleaner는 Claude Code, claude-sonnet-5-5, --effort high, runs 1, Codex 채점자를 썼고 policy/eval-policy.json의 DEFAULT_RUNS는 3이다. 이 값들을 다시 쓸까?
- PDF 입력. 1번, 2번, 3번 사례의 PDF를 어디서 가져올까? 입력을 추가하면 eval set 변경이다.
- pdftotext. 실행 환경에 pdftotext가 있다고 보장하는가? SKILL.md는 이 도구를 쓰지만 없을 때 어떻게 할지 말하지 않는다.
- 핵심 줄 수. SKILL.md 2단계는 섹션마다 최대 3줄이라 하고 references/format.md와 SKILL.md Example은 전체 핵심 3줄이라 한다. 어느 쪽을 따를까?
