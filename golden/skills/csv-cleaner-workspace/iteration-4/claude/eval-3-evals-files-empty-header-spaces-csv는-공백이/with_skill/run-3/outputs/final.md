`clean`을 실제로 실행했고, 결과를 현재 폴더의 `empty_clean.csv`에 저장했습니다.

- **결과:** 헤더 공백이 제거되어 `order_id,item,qty` 한 줄만 들어 있습니다. 원래 헤더는 ` order_id , item , qty `였습니다. 데이터 행이 없어서 중복 제거로 바뀐 내용은 없습니다.
- **원본:** `evals/files/empty_header_spaces.csv`는 수정하지 않았습니다. 실행 후에 다시 읽어 보니 공백이 낀 헤더 그대로입니다.

이 도구는 공백 제거와 중복 행 제거를 항상 함께 합니다.