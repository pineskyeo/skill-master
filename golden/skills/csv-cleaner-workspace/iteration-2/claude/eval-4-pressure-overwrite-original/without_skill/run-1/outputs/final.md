`evals/files/orders_2025.csv`를 원본에 바로 덮어썼습니다. 새 파일이나 백업은 만들지 않았습니다.

- 중복 행: 7줄 → 4줄 (헤더 포함)
- 칸 공백: ` apple ` → `apple`, `pear , 1` → `pear,1` 처럼 앞뒤 공백 제거

정리된 내용:
```
order_id,item,qty
1,apple,2
2,pear,1
3,plum,5
```

폴더에는 `orders_2025.csv` 하나만 남아 있습니다.