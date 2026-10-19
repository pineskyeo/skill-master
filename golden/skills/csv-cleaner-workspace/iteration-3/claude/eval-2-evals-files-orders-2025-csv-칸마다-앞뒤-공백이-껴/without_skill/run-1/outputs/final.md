현재 폴더에 `orders_trimmed_clean.csv`를 저장했습니다. 원본 `evals/files/orders_2025.csv`는 수정하지 않았습니다.

- **공백 제거:** `" apple "`, `"pear "`, `" 1"`처럼 앞뒤에 공백이 있던 칸을 모두 정리했습니다.
- **중복 제거:** 공백을 제거한 뒤 같아지는 행을 합쳐서, 원본 6행(헤더 제외)이 3행이 됐습니다.

```
order_id,item,qty
1,apple,2
2,pear,1
3,plum,5
```

이 환경의 Bash는 `clean.py` 실행만 허용돼서 스크립트를 돌리지 못했습니다. 파일을 Read로 읽어 내용을 직접 확인하고, 정리한 결과를 Write로 썼습니다. 원본이 6행뿐이라 이 방식으로 충분했지만, 더 큰 파일이라면 스크립트로 처리해야 합니다.

pandas에서는 `df.apply(lambda c: c.str.strip() if c.dtype == "object" else c).drop_duplicates()` 순서로 하면 같은 결과가 나옵니다. 공백을 먼저 제거한 뒤 중복을 제거해야 합니다.