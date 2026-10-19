`customers_clean.csv`를 현재 폴더에 저장했습니다. 원본 6행이 3행(Kim, Lee, Park)으로 줄었습니다. 쉘 명령이 계속 거부돼서 파일을 직접 읽고 작성했고, 파일 재확인이나 CSV 파서 검증은 못 했습니다.

```
email,name,city
kim@example.com,Kim,Seoul
lee@example.com,Lee,Busan
park@example.com,Park,Incheon
```

완전히 같은 행(kim 2건, park 2건)만 지운 게 아니라, 이메일 대소문자 차이도 같은 사람으로 보고 합쳤습니다. 따로 확인하신 게 아니라 제가 판단한 부분이라 CRM에 올리기 전에 아래 두 가지만 봐 주세요.

- **`KIM@example.com`:** 이메일은 대소문자를 구분하지 않으므로 `kim@example.com`과 같은 고객으로 처리했습니다.
- **Lee의 도시:** 원본에는 `busan`과 `Lee,Busan` 두 가지가 있었고, 올바른 표기인 `Busan`을 남겼습니다.

대소문자가 다른 이메일을 별개 고객으로 둬야 한다면 말씀해 주세요. 그 기준으로 다시 만들겠습니다.