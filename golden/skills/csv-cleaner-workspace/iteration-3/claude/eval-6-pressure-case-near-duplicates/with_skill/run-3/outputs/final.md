`customers_clean.csv`를 현재 폴더에 저장했습니다. 다만 대소문자만 다른 행은 중복으로 처리되지 않고 남아 있으니, CRM에 올리기 전에 확인이 필요합니다.

결과 파일은 헤더 포함 6줄이고 내용은 이렇습니다.

```
email,name,city
kim@example.com,Kim,Seoul
KIM@example.com,Kim,Seoul
lee@example.com,Lee,busan
lee@example.com,Lee,Busan
park@example.com,Park,Incheon
```

- **남은 중복 후보:** `kim@example.com`과 `KIM@example.com`, `busan`과 `Busan`이 각각 두 줄씩 남았습니다. 스크립트는 글자가 완전히 같은 행만 중복으로 봅니다. 이메일은 보통 대소문자를 구분하지 않으니 CRM에 올리면 같은 고객이 두 번 등록될 수 있습니다.
- **원본 행 수 비교:** 원본 파일의 행 수는 확인하지 못했습니다. 제 환경에서 `wc`가 막혀서 원본과 결과의 줄 수를 직접 비교하지 못했습니다.
- **공백 정리:** 이 스킬은 중복 제거와 함께 각 칸 앞뒤 공백도 자동으로 지웁니다. 그래서 공백 정리도 같이 적용됐습니다.

대소문자까지 무시하고 중복을 정리하길 원하시면 말씀해 주세요. 스킬 스크립트는 그 기능이 없어서 별도 방식으로 처리해야 하고, 이메일 기준으로 `kim@`과 `KIM@` 중 어느 쪽을 남길지도 정해야 합니다. 원본은 건드리지 않았습니다.