`empty_clean.csv`를 현재 폴더에 만들었습니다. 헤더의 공백이 없어지고 데이터 행은 없는 상태입니다.

- **원본** (`evals/files/empty_header_spaces.csv`): ` order_id , item , qty `
- **결과** (`empty_clean.csv`): `order_id,item,qty`

원본은 수정하지 않았고, 두 파일을 다시 읽어서 위 내용을 확인했습니다. 이 스킬은 칸 공백 제거와 중복 행 제거를 항상 함께 실행합니다. 이 파일은 데이터 행이 없어서 중복 제거로 바뀐 내용은 없습니다.