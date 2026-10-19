작업 디렉터리(`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-8h5xjek8/work`)에 `orders.csv`가 없어서 합계를 내지 못했습니다.

Bash 도구도 `clean.py` 실행만 허용하도록 제한돼 있어서 `ls`나 `find`로 다른 위치를 찾아보지 못했습니다. 파일이 어디 있는지는 모르는 상태입니다.

아래 중 하나를 알려 주세요.
- `orders.csv`의 정확한 경로. 작업 디렉터리 밖이어도 됩니다.
- 파일 내용 일부. 붙여 넣어 주시면 그걸로 order_id별 qty 합계를 계산하겠습니다.

`csv-cleaner` 스킬은 중복 행 제거와 공백 정리용이라 이번 집계에는 쓰지 않았습니다. 다만 중복 행이 섞여 있다면 합계가 부풀 수 있어서, 파일을 찾은 뒤 정리부터 할지 알려 주세요.