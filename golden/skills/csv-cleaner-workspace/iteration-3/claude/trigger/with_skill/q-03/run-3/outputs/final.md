CSV 정리는 못 했습니다. 작업 디렉터리에 `data/raw_leads.csv`가 없습니다.

- 정리 스크립트는 "input must be a regular workdir file without aliases"라는 오류를 내고 입력을 거부했습니다.
- Read로 `data/raw_leads.csv`와 `data` 디렉터리를 열어 봤는데 둘 다 존재하지 않는다고 나왔습니다.
- 이 세션에서는 Bash가 정리 스크립트 호출 외에는 막혀 있어서 `ls`로 주변 파일을 확인하지 못했습니다.

작업 디렉터리는 `/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-5iqo84n2/work`입니다.

1. 파일이 다른 경로에 있다면 정확한 경로를 알려 주세요. 단, 작업 디렉터리 안에 있는 일반 파일이어야 스크립트가 받아 줍니다. 심볼릭 링크나 하드 링크는 거부됩니다.
2. 파일이 작업 디렉터리 밖에 있다면 안으로 복사해 주세요.

정리 결과는 원본을 덮어쓰지 않고 `data/raw_leads.clean.csv` 같은 새 경로에 저장합니다. 이 스킬은 공백 제거와 중복 행 제거를 항상 함께 합니다. 요청하신 두 가지가 바로 그 두 가지라 문제는 없습니다.