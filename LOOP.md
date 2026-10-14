# 피드백 루프 프로토콜

1. **제출**: 빌더 에이전트가 스킬/에이전트 폴더를 `submissions/<name>/`에 만든다.
2. **검사**: `python3 eva.py check <path>` (또는 `--json`).
3. **피드백**: FAIL이면 `python3 eva.py feedback <path> --round N` → `FEEDBACK.md`/`FEEDBACK.json`. [필수] 항목부터 고친다.
4. **재제출**: 빌더가 FEEDBACK.md의 "수정" 지시만 반영해 다시 제출. 최대 3라운드.
5. **종료**: PASS면 `golden/` 또는 운영 폴더로 옮기고 `eva.py index`로 색인 갱신. 3라운드 후에도 FAIL이면 사람에게 넘긴다.

자동화: `python3 loop.py <path> --builder "<빌더 실행 명령>" --max-rounds 3`
(빌더는 환경변수 `EVA_FEEDBACK`, `EVA_ROUND`를 받는다.)

실행 기록 채점: `python3 eva.py check <agent_path> --transcript run.json`
run.json 형식: `{"scenario": "<id>", "tool_calls": [...], "final": "<최종 답>"}`
→ 선언 안 된 도구 사용, 기대 도구 누락, 시나리오 `must_include` 키워드 누락을 잡는다.
