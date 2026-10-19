sales.csv의 날짜 형식은 아직 바꾸지 못했습니다. 작업 디렉터리에서 파일을 찾지 못했기 때문입니다.

- `Read`로 `/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-ubeypnuu/work/sales.csv`를 열었더니 파일이 없다는 오류가 났습니다.
- 이 세션에서는 Bash가 설치된 `clean.py`를 경로 두 개와 함께 호출하는 명령만 허용합니다. 그래서 `ls`나 `find`로 디렉터리를 확인하지 못했습니다. `clean.py`는 중복 행 제거와 공백 정리용이라 날짜 형식 변환에는 맞지 않아 쓰지 않았습니다.

파일이 다른 위치에 있다면 전체 경로를 알려주세요. 파일이 아직 이 디렉터리에 없다면 넣어주세요. 파일이 확인되면 `Edit`으로 `YYYY/MM/DD`를 `YYYY-MM-DD`로 바꾸겠습니다. 날짜가 아닌 열에 `/`가 들어 있을 수 있으니, 날짜 열만 정확히 골라서 바꾸겠습니다.