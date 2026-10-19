`old.csv`와 `new.csv`가 작업 디렉터리(`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-wkh3iw8u/work`)에 없어서 비교하지 못했습니다. 두 파일 모두 읽기를 시도했지만 "File does not exist" 오류가 났습니다.

이 환경에서는 Bash도 `clean.py` 호출만 허용돼서 `ls`나 `find`로 다른 위치를 찾아볼 수 없었습니다. 그리고 `clean.py`(csv-cleaner)는 중복 제거와 공백 정리용이라 두 파일을 비교하는 데는 쓸 수 없습니다.

두 파일의 전체 경로를 알려주시거나 작업 디렉터리로 옮겨주세요. 그러면 Read로 두 파일을 읽어서 추가, 삭제, 변경된 행만 정리해 드리겠습니다. 행이 어떤 컬럼으로 대응되는지(예: `id` 컬럼 기준)도 알려주시면 "변경"과 "삭제 후 추가"를 구분하는 데 도움이 됩니다.