# korean-proofreader 요구사항

## 하는 일
- 한국어 문장이나 문서(text)를 받아 맞춤법과 띄어쓰기를 고친다. 표기 기준은 열린 질문의 맞춤법 기준 참고.
- 고친 글 corrected_text(한국어)와 바뀐 부분 목록 changes를 낸다. 내는 곳은 열린 질문의 출력 위치 참고.
- changes에는 띄어쓰기 고침과 맞춤법 고침을 각각 적는다 (SKILL.md Example).
- 띄어쓰기와 맞춤법만 고치고 내용과 뜻은 바꾸지 않는다 (evals.json 2번 사례).
- 고칠 곳이 없으면 고칠 것이 없다고 말하고 원문을 그대로 돌려주며 changes는 비운다 (evals.json 3번 사례).

## 써야 할 때 / 쓰지 말아야 할 때
- 사용: 한국어 글의 교정, 맞춤법 고치기, 띄어쓰기 고치기. 대상은 문장, 문단, 문서 파일, 자기소개서, 보고서, 에세이, 블로그 글, 이메일, 제품 설명.
- 비사용: 영어 글 교정, 일본어·중국어 문법 검사, 한국어 글을 영어로 번역, 한국어 기사 요약, PDF 요약, 더 공손한 말투로 고쳐 쓰기, 발음 묻기, 이름 로마자 표기, 한국어 글 새로 쓰기.

## 첫 실제 실행 설정
- 시험 사례: evals.json 3개, 평가 항목 8개 (static regex 5개, llm 문자열 3개).
- 입력 파일: 사례마다 하나씩 evals/files/short_phrase.txt, evals/files/report_draft.txt, evals/files/weather_note.txt.
- 트리거 평가: evals/trigger_evals.json의 should-use 10개, should-not 10개.
- 엔진, 모델, effort, runs, 채점자는 스킬 파일에 없다. 열린 질문의 실행 설정 참고.
- 첫 실제 실행은 static matcher 5개를 바꾸기 전에는 시작할 수 없다. pinesky는 이 스킬의 실행 출력 matcher에 한국어를 허용하지 않기로 하고(2026-10-31 21:57 KST)
  english-output-matchers 항목을 기각했다. skill_eval.py의 live rule_review 게이트(_live_rule_review)는 이 항목이 있고 rule_review 기록이 없으면 실행을 거부한다.

## 통과 기준 (하네스 게이트)
- with_skill 평균 pass_rate가 without_skill보다 낮지 않다 (회귀 없음).
- with_skill 트리거 평가: should-use 10개 trigger_rate ≥ 0.5, should-not 10개 < 0.5.
- with_skill run의 static 검사 실패가 없다. 대상은 static regex 5개이고, 첫 실제 실행 전에 모두 바꿔야 한다.
- 실행 오류·격리 위반·미채점 항목이 없다. llm 3개도 모두 채점되어야 한다.

## pinesky에게 묻는 열린 질문
- 이 파일은 pinesky 검토용 초안이고, 승인은 기록되어 있지 않다.
- 실행 설정. 스킬 파일에 엔진, 모델, effort, runs, 채점자가 없다. csv-cleaner는 Claude Code, claude-sonnet-5-5, --effort high, runs 1, Codex 채점자를 썼고 policy/eval-policy.json의 DEFAULT_RUNS는 3이다. 이 값들을 다시 쓸까?
- matcher 교체. static check.pattern 5개를 어떤 방식으로 바꿀까? 계획은 pull request 본문에 있다.
- 맞춤법 기준. SKILL.md는 표기 기준을 정하지 않았고 evals.json 52행 패턴은 report_draft.txt의 명사를 띄어 쓴 형태와 붙여 쓴 형태(전년 대비, 전년대비)를 모두 받는다. 어느 쪽을 맞는 표기로 볼까?
- 출력 위치. SKILL.md Outputs는 corrected_text를 답변에 낼지 파일에 쓸지 말하지 않는데 evals.json 사례 3개의 프롬프트는 모두 답변(Reply)을 요구한다. 어느 쪽으로 정할까?
