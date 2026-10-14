# 피드백 루프 프로토콜

1. **제출**: 빌더 에이전트가 스킬/에이전트 폴더를 `submissions/<name>/`에 만든다.
2. **검사**: `python3 eva.py check <path>` (또는 `--json`).
3. **피드백**: FAIL이면 `python3 eva.py feedback <path> --round N` → `FEEDBACK.md`/`FEEDBACK.json`. [필수] 항목부터 고친다.
4. **재제출**: 빌더가 FEEDBACK.md의 "수정" 지시만 반영해 다시 제출. 최대 3라운드.
5. **종료**: PASS면 `golden/` 또는 운영 폴더로 옮기고 `eva.py index`로 색인 갱신. 3라운드 후에도 FAIL이면 사람에게 넘긴다.

자동화: `python3 loop.py <path> --builder "<빌더 실행 명령>" --max-rounds 3`
(빌더는 환경변수 `EVA_FEEDBACK`, `EVA_ROUND`를 받는다. 빌더가 0이 아닌 코드로 끝나면 루프를 멈추고 종료코드 2로 실패를 알린다.)

실행 기록 채점: `python3 eva.py check <agent_path> --transcript run.json`

run.json 형식 (JSON 객체):
```json
{
  "scenario": "<시나리오 id>",
  "workspace": "/abs/path/to/repo",
  "tool_calls": [
    "web_search",
    {"tool": "web_fetch", "input": {"url": "https://example.org/a"}, "result": "<도구가 돌려준 내용>"}
  ],
  "tool_results": ["<도구 결과 텍스트>", "..."],
  "final": "<최종 답>"
}
```
- 필수: `scenario`(문자열), `tool_calls`(목록), `final`(문자열). 선택: `workspace`, `tool_results`.
- `tool_calls` 항목은 도구 이름 문자열, 또는 `{"tool": str, "input": str|object(선택), "result": str(선택)}` 객체. 그 밖의 필드나 타입은 형식 오류.
- 도구 결과는 각 호출의 `result`에 붙이거나, 최상위 `tool_results`(문자열 목록)에 모아 둔다. 둘 다 써도 된다.
- 출처 조건에서는 **도구 결과에 실제로 나온 URL만** 센다. 결과에 없는 URL(지어낸 URL)은 세지 않는다.
- 파일:줄 근거는 **그 줄이 도구 결과에 줄 번호와 함께 나왔을 때만** 센다(grep `-n`의 `경로:줄:내용`, 또는 그 호출 `input`의 파일에 대한 `cat -n`/Read 형식 `줄<탭>내용`). 없는 줄은 세지 않는다.
- 자동 확인이 안 되는 기준은 needs-review로 나오고, 자동 검사를 다 통과해도 판정은 PASS가 아니라 REVIEW(종료코드 3)다.
- `workspace`가 있으면 그 밖의 절대경로 접근도 `no-answer-lookup` 위반이다.
- 형식이 틀리면 `transcript-valid` [필수] 실패로 FAIL 보고서가 나온다(예외로 죽지 않음).

→ 잡는 것(모두 [필수]): 선언 안 된 도구 사용, 기대 도구 누락, 시나리오 `must_include` 키워드 누락, 정답 조회(`no-answer-lookup`: 워크스페이스 밖 탐색, git 이력 뒤지기), 그리고 success_criteria/expected_outcome 결과 미달(예: 검증된 출처 URL 개수, 파일:줄 근거 개수, 파일 변경 0건, 글자 수, 비교표).
