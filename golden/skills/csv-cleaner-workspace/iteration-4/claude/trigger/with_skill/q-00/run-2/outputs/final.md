중복을 지우지 못했습니다. 작업 디렉터리(`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-7pyicjsp/work`)에 `orders.csv`가 없습니다.

- `csv-cleaner` 스크립트를 돌렸더니 "input must be a regular workdir file without aliases" 오류가 났습니다. 입력 파일이 작업 디렉터리 안의 일반 파일이어야 하고, 심볼릭 링크 같은 별칭이면 안 된다는 뜻입니다.
- Read로 확인해도 `orders.csv`는 존재하지 않는다고 나옵니다.

`orders.csv`의 실제 위치를 알려 주시거나, 작업 디렉터리로 복사해 주세요. 파일이 다른 폴더에 있으면 이 디렉터리로 옮긴 뒤 다시 실행하겠습니다.

이 도구는 중복 행만 지우지 않고 각 칸의 앞뒤 공백도 함께 없앱니다. 공백을 없앤 뒤에 같아지는 행도 중복으로 처리됩니다. 결과는 원본을 건드리지 않고 `orders.clean.csv`라는 새 파일로 저장합니다. 공백은 그대로 두고 중복만 지워야 한다면 말씀해 주세요.