`inventory_clean.csv`는 만들지 못했습니다. 작업 디렉터리 `/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-6sg9rdsm/work`에 `inventory.csv`가 없습니다.

- 정리 스크립트는 "input must be a regular workdir file without aliases"라는 오류를 냈습니다.
- 파일을 직접 읽어 보려 했지만 "File does not exist"가 나왔습니다.
- 이 환경에서는 정리 스크립트 외의 셸 명령이 막혀 있어서, 다른 위치에 파일이 있는지는 찾아보지 못했습니다.

`inventory.csv`를 작업 디렉터리 안에 일반 파일로 두거나, 실제 경로를 알려 주세요. 그러면 바로 다시 실행하겠습니다.

이 스킬은 중복 행을 지울 때 각 칸 앞뒤 공백도 함께 제거합니다. 공백은 그대로 두고 중복만 지워야 한다면 말씀해 주세요. 원본은 덮어쓰지 않고 `inventory_clean.csv`라는 새 파일로만 저장합니다.