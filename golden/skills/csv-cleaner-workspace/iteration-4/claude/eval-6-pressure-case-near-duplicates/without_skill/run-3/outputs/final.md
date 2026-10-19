`customers_clean.csv`를 현재 폴더에 저장했습니다. 원본 6행이 3행으로 줄었습니다. 업로드 전에 아래 두 가지 판단은 확인해 보세요.

```
email,name,city
kim@example.com,Kim,Seoul
lee@example.com,Lee,Busan
park@example.com,Park,Incheon
```

- **이메일 대소문자 무시:** 완전히 같은 행(kim, park) 외에 `KIM@example.com`도 `kim@example.com`과 같은 사람으로 보고 합쳤습니다. 이메일은 대소문자를 구분하지 않는 게 일반적이고, CRM에서 중복으로 걸릴 수 있어서입니다.
- **lee의 city:** `busan`과 `Busan` 두 행 중 대문자로 시작하는 `Busan`을 남겼습니다.

원본 `evals/files/customers_case.csv`는 건드리지 않았습니다. 대소문자만 다른 행까지 지우는 게 싫으시면 완전히 같은 행만 지우는 버전으로 다시 만들어 드리겠습니다.