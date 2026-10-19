`customers_clean.csv`를 현재 폴더에 저장했습니다. 원본 6행이 3행으로 줄었습니다. Bash 실행이 거부되어 Read로 원본을 읽고 Write로 직접 작성했으니, 원본과 한 번 대조해 보세요.

```
email,name,city
kim@example.com,Kim,Seoul
lee@example.com,Lee,Busan
park@example.com,Park,Incheon
```

완전히 같은 행만 지운 것이 아니라 아래 두 가지를 판단해서 처리했습니다. CRM에 올리기 전에 확인해 주세요.
- **이메일 대소문자:** `KIM@example.com`과 `kim@example.com`은 같은 사람으로 보고 합쳤습니다. 이메일은 소문자로 통일했습니다.
- **도시 표기:** lee의 `busan`과 `Busan`은 `Busan` 하나로 합쳤습니다. 먼저 나온 행이 소문자라서, 도시는 첫 글자를 대문자로 맞췄습니다.

대소문자만 다른 행은 별개 고객으로 두어야 한다면 말씀해 주세요. 그 기준으로 다시 만들겠습니다.