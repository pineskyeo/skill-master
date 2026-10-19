현재 폴더에 `customers_clean.csv`를 저장했습니다. 원본 6행이 3행으로 줄었어요.

```
email,name,city
kim@example.com,Kim,Seoul
lee@example.com,Lee,Busan
park@example.com,Park,Incheon
```

- **완전 중복:** kim이 2행, park가 1행 제거됐습니다.
- **대소문자만 다른 중복:** `KIM@example.com`은 kim과 같은 고객으로 보고 제거했습니다. lee는 `busan`과 `Busan` 두 행이 있었는데, `Busan` 쪽을 남겼습니다.

업로드 전에 한 가지만 확인해 주세요. 이메일은 대소문자를 구분하지 않는다고 보고 합쳤습니다. 대소문자가 다른 이메일을 별개 고객으로 관리하는 CRM이라면 알려 주세요. 다시 만들겠습니다.

`clean.py`는 Bash가 설치된 경로의 호출만 허용해서 실행하지 못했습니다. 그래서 `clean.py`를 거치지 않고 파일을 직접 작성했고, 원본은 건드리지 않았습니다.