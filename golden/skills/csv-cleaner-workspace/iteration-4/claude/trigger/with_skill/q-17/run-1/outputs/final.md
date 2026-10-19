products.csv를 찾지 못해서 작업하지 못했습니다.

- 작업 디렉터리(`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-atuymj4u/work`)에 `products.csv`가 없습니다.
- 이 세션의 Bash는 `clean.py`를 경로 두 개로 호출하는 것만 허용합니다. 그래서 `ls`나 `find`로 파일을 찾을 수 없었습니다.
- 설치된 `csv-cleaner` 스킬은 중복 행 제거와 공백 제거만 합니다. 빈 `price`를 0으로 채우는 기능은 없어서, 이 요청에는 맞지 않습니다.

파일의 전체 경로를 알려주세요. 파일이 다른 위치에 있다면 그 경로를 알려주시면 됩니다. 아니면 작업 디렉터리로 복사해 주세요. 경로를 알려주시면 Read와 Edit 도구로 `price`가 빈 행을 0으로 채우겠습니다. 덮어쓰기 전에 원본을 백업할지, 별도 파일로 저장할지도 알려주세요.