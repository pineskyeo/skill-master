`customers_clean.csv`를 현재 폴더에 저장했습니다. 원본 6행이 3행으로 줄었습니다.

```
email,name,city
kim@example.com,Kim,Seoul
lee@example.com,Lee,Busan
park@example.com,Park,Incheon
```

판단이 들어간 부분이 두 가지 있어서, 올리기 전에 확인하세요.
- **이메일 대소문자:** `KIM@example.com`과 `kim@example.com`은 같은 이메일로 보고 하나로 합쳤습니다. 이메일은 대소문자를 구분하지 않기 때문입니다.
- **Lee의 도시:** `busan`과 `Busan`이 있었습니다. 다른 도시 표기와 맞춰서 `Busan` 쪽을 남겼습니다.

도시 표기만 다른 행을 같은 사람으로 본 것이라, 실제로는 동명이인 같은 다른 고객이면 되돌려야 합니다. 원본은 수정하지 않았습니다.