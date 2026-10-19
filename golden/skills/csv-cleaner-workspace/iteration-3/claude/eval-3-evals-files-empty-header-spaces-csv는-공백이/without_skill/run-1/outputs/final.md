clean을 실행하지 못해서 `empty_clean.csv`는 만들지 못했습니다. 원본은 읽기만 했고 수정하지 않았습니다.

- **막힌 부분:** 이 환경의 Bash는 "설치된 clean.py를 경로 두 개로 호출하는 것"만 허용합니다. 그런데 설치된 clean.py의 위치를 알 수 없습니다. `python3 clean.py …`와 `clean.py …` 둘 다 같은 이유로 거부됐습니다. `ls`와 `find`도 거부됐고, 서브에이전트에게 맡긴 탐색도 마찬가지였습니다. 작업 폴더에는 `.claude/skills`가 없습니다.
- **원본 내용:** `evals/files/empty_header_spaces.csv`는 1행이 ` order_id , item , qty `이고 데이터 행은 없습니다. 헤더에 공백이 있다는 말씀과 일치합니다.
- **하지 않은 일:** 직접 trim한 결과를 `empty_clean.csv`로 쓰는 방법은 쓰지 않았습니다. clean이 실제로 무엇을 출력하는지(헤더 정리 방식, 빈 파일 처리 등)는 실행해 봐야 알 수 있어서, 제가 추측해서 쓰면 "실제 실행 결과"가 아니게 됩니다.

다음 중 하나를 알려주시면 이어서 하겠습니다.
1. clean.py의 절대 경로를 알려주시면, 그 경로로 `clean.py evals/files/empty_header_spaces.csv empty_clean.csv` 형태로 호출해 보겠습니다.
2. 직접 실행하셔도 됩니다. 그 결과를 알려주시면 확인만 하겠습니다.
3. 실행 없이 제가 만든 정리본으로 저장해도 된다고 하시면 그렇게 하겠습니다. 다만 clean의 실제 결과와 다를 수 있습니다.