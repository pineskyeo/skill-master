`data/raw_leads.csv`를 정리하지 못했습니다. 작업 디렉터리에 이 파일이 없습니다. `data` 폴더도 없고, Read로 확인했을 때 둘 다 존재하지 않는다고 나왔어요. 정리 스크립트도 "input must be a regular workdir file without aliases"라며 입력을 거부했습니다. 출력 파일(`data/raw_leads.clean.csv`)은 만들어지지 않았고, 원본은 건드리지 않았어요.

이 스킬에서 허용된 Bash 명령은 정리 스크립트 실행뿐이라 `ls`로 다른 위치를 찾아볼 수도 없었습니다.

현재 작업 디렉터리는 `/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-8q8julvx/work`입니다. 아래 중 하나를 알려주세요.
- 파일의 실제 경로. 이 디렉터리 밖에 있다면 작업 디렉터리 안으로 복사해 주세요. 스크립트가 작업 디렉터리 밖의 파일이나 심볼릭 링크 같은 별칭은 받지 않는 것 같습니다.
- 경로가 맞다면, 파일이 아직 생성되지 않았거나 다른 위치에 있을 수 있으니 확인해 주세요.

파일이 확인되면 `data/raw_leads.clean.csv`로 다시 정리하겠습니다. 이 스킬은 공백 제거와 중복 행 제거를 항상 함께 처리합니다.