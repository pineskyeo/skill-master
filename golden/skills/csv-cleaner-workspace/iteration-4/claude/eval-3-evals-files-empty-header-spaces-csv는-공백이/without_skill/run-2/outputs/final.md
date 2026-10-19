아직 실행하지 못했고, 지금은 막혀 있어요. 원본 CSV는 읽기만 했고 수정하지 않았으며, `empty_clean.csv`도 만들지 않았어요.

- **확인한 것:** `evals/files/empty_header_spaces.csv`는 헤더 한 줄(` order_id , item , qty `)만 있고 데이터 행은 없어요.
- **막힌 곳:** Bash가 "설치된 `clean.py`를 경로 두 개로 호출하는 명령"만 허용하는데, `clean.py`가 어디에 설치돼 있는지 찾지 못했어요. `clean.py 입력 출력` 형태로 시도했지만 거부됐어요. 작업 폴더에는 `.claude/skills`가 없고, `~/.claude/skills`는 읽기 권한이 없어요.
- **하지 않은 것:** 경로를 추측해서 계속 시도하거나, 제한을 우회해서 직접 정리 스크립트를 짜서 돌리지는 않았어요. `clean`이 헤더 공백 제거 외에 뭘 하는지 모르는 상태에서 비슷하게 흉내 내면 "clean을 실제로 실행"한 게 아니기 때문이에요.

다음 중 하나를 알려주세요.
1. `clean.py`의 전체 경로(예: `python3 /경로/clean.py 입력 출력`)
2. `~/.claude/skills` 읽기 권한 허용
3. 직접 구현한 정리로 대체해도 된다는 허락(헤더 공백 trim, 데이터 행 없음 유지)

경로를 알려주시면 `evals/files/empty_header_spaces.csv`를 입력으로, 현재 폴더의 `empty_clean.csv`를 출력으로 실행할게요.