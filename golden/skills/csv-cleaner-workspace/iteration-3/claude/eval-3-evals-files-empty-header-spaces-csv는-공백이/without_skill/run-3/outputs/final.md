`clean`을 실행하지 못해서 `empty_clean.csv`는 만들지 않았습니다.

- **막힌 부분:** Bash 도구가 `clean.py`를 두 경로 인자로 호출하는 명령만 허용합니다. `clean.py <입력> <출력>`과 `python3 clean.py <입력> <출력>`을 시도했지만 둘 다 "only the installed clean.py invocation with two paths is permitted"라는 메시지로 거부됐습니다. 설치된 `clean.py`의 위치를 모르기 때문에 허용되는 호출 형태를 맞추지 못했습니다. `ls`나 `find`로 위치를 찾는 명령도 모두 거부됐고, `~/.claude/skills`는 읽기 권한이 없습니다.
- **입력 파일:** `evals/files/empty_header_spaces.csv`는 읽었습니다. 헤더 한 줄(` order_id , item , qty `)만 있고 데이터 행은 없습니다. 원본은 수정하지 않았습니다.
- **직접 만들지 않은 이유:** 헤더 공백을 제거한 결과를 제가 직접 써서 `empty_clean.csv`로 저장할 수는 있습니다. 하지만 `clean`이 빈 데이터를 어떻게 처리하는지(헤더만 남기는지, 빈 파일을 만드는지, 오류를 내는지) 확인하지 못한 채 만든 파일이 됩니다. 그래서 `clean`을 실행한 결과인 것처럼 저장하지 않았습니다.

아래 중 하나를 알려주시면 이어서 하겠습니다.
1. 설치된 `clean.py`의 전체 경로. 그 경로로 호출해 보겠습니다.
2. `~/.claude/skills` 등 해당 위치의 읽기 권한.
3. 직접 처리해도 된다는 확인. 이 경우 헤더 공백만 제거해 `empty_clean.csv`를 만들고, `clean`을 실행한 결과가 아니라는 점을 함께 적겠습니다.