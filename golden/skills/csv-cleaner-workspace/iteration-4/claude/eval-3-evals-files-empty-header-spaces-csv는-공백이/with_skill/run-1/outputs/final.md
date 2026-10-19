`empty_clean.csv`를 현재 폴더에 저장했습니다. 원본은 건드리지 않았습니다.

- **정리 결과:** 헤더가 ` order_id , item , qty `에서 `order_id,item,qty`로 바뀌었습니다. 칸 앞뒤 공백이 없어졌습니다. 데이터 행이 없어서 중복 제거할 행은 없었습니다.
- **원본:** `evals/files/empty_header_spaces.csv`는 공백이 낀 헤더 그대로입니다.
- **실행한 명령:** `python3 -I .claude/skills/csv-cleaner/scripts/clean.py evals/files/empty_header_spaces.csv empty_clean.csv`. 오류 없이 끝났고, 두 파일 내용은 따로 읽어서 확인했습니다.