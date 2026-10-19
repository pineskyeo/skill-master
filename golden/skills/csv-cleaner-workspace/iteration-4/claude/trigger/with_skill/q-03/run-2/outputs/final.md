`data/raw_leads.csv`를 정리하지 못했습니다. 그 경로에 파일이 없습니다.

- csv-cleaner 스크립트는 `input must be a regular workdir file without aliases`라는 오류를 내고 멈췄습니다.
- Read로 확인했더니 `/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-gr5ot2bx/work/data/raw_leads.csv`가 없다고 나왔습니다.
- Bash는 정리 스크립트 호출만 허용되어 있어서 `ls`로 `data/` 폴더를 열어 보지 못했습니다. 그래서 파일이 다른 곳에 있는지, 폴더 이름이 다른지는 확인하지 못했습니다.
- 출력 파일은 만들어지지 않았고 원본도 건드리지 않았습니다.

파일의 정확한 경로를 알려 주시면 그 경로로 다시 돌리겠습니다. 파일이 이 작업 디렉터리 밖에 있다면 작업 디렉터리 안으로 옮겨 주셔야 합니다. 출력은 `data/raw_leads.clean.csv`처럼 새 경로로 저장하고 원본은 덮어쓰지 않습니다.