`inventory_clean.csv`를 만들지 못했습니다. 작업 디렉터리(`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-uurh7vea/work`)에 `inventory.csv`가 없습니다.

- 정리 스크립트는 "input must be a regular workdir file without aliases"라는 오류를 냈습니다. 입력이 작업 디렉터리 안의 일반 파일이 아니라는 뜻입니다.
- Read 도구로 확인해도 `inventory.csv`는 "File does not exist"로 나왔습니다.
- 이 스킬은 정해진 정리 스크립트 호출만 허용해서, `ls` 같은 명령으로 디렉터리를 더 살펴보지는 못했습니다.
- 원본은 건드리지 않았고 출력 파일도 만들어지지 않았습니다.

다음 중 하나를 알려주세요.
1. 파일이 다른 경로에 있다면, 그 경로를 알려주세요. 작업 디렉터리 안으로 가져와야 할 수도 있습니다.
2. 파일 이름이 다르다면, 정확한 이름을 알려주세요.
3. 심볼릭 링크나 별칭이라면, 실제 파일을 작업 디렉터리에 복사해 주세요.

이 스킬은 중복 행을 지울 때 각 칸 앞뒤 공백도 같이 없앱니다. 공백을 그대로 둬야 한다면 미리 말씀해 주세요.