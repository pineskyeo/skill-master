`customers_clean.csv`를 현재 폴더에 저장했어요. 원본 7행이 고객 3명으로 줄었습니다.

```
email,name,city
kim@example.com,Kim,Seoul
lee@example.com,Lee,Busan
park@example.com,Park,Incheon
```

- **완전 중복:** `kim@example.com`과 `park@example.com`이 각각 한 번씩 더 있어서 지웠습니다.
- **대소문자 차이:** `KIM@example.com`은 `kim@example.com`과 같은 사람으로 보고 합쳤습니다. `lee`는 `busan`과 `Busan`이 있었는데 `Busan`을 남겼습니다.
- **직접 작성한 파일:** Bash에서는 `clean.py`가 실행되지 않아서(설치 경로를 알 수 없었어요) 7행짜리 파일이라 제가 직접 써서 저장했습니다. 스크립트 결과가 아닙니다.
- **업로드 전 확인:** 대소문자 차이를 같은 고객으로 본 건 제 판단이에요. CRM이 이메일을 대소문자 구분해서 따로 저장하는 설정이면 `KIM@example.com`과 `busan` 행을 되살려야 할 수 있습니다. 그런 설정이 아니라면 그대로 올려도 됩니다.