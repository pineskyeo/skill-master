`customers_clean.csv`를 현재 폴더에 저장했어요. 원본 7행이 3행이 됐습니다.

```
email,name,city
kim@example.com,Kim,Seoul
lee@example.com,Lee,Busan
park@example.com,Park,Incheon
```

CRM에 올리기 전에 아래 두 가지만 알아두세요.

- **대소문자를 무시하고 중복을 판단했어요.** `KIM@example.com`과 `kim@example.com`은 같은 고객으로 보고 합쳤습니다. 이메일은 소문자로 통일했어요.
- **city 표기를 맞췄어요.** `busan`과 `Busan`이 달라서 중복으로 안 잡히던 Lee 건은 `Busan`으로 통일해서 한 행만 남겼습니다.

파이썬 스크립트 실행이 승인되지 않아서, 원본이 7행뿐이라 결과를 직접 파일로 작성했어요. 원본 파일은 건드리지 않았습니다.