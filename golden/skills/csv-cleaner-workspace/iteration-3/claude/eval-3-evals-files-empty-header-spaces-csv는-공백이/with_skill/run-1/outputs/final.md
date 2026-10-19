clean을 실행해서 `empty_clean.csv`를 현재 폴더에 만들었어요. 원본은 그대로예요.

- **결과:** 헤더가 `order_id,item,qty`로 정리됐어요. 데이터 행은 원래 없었고, 지금도 없어요.
- **원본:** `evals/files/empty_header_spaces.csv`는 ` order_id , item , qty ` 그대로 공백이 남아 있어요.
- **출력 경로:** 처음에 `./empty_clean.csv`로 실행했더니 "output must be a new workdir file without aliases" 오류가 났어요. 그래서 `empty_clean.csv`로 경로만 바꿔 다시 실행했고, 이번에는 성공했어요.

이 스킬은 칸 앞뒤 공백 제거와 중복 행 제거를 항상 같이 해요. 이번 파일은 데이터 행이 없어서 중복 제거는 영향이 없었어요.