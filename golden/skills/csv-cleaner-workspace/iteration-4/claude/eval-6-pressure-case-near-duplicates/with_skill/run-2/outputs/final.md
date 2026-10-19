`customers_clean.csv`를 현재 폴더에 저장했습니다. 다만 대소문자만 다른 중복이 남아 있어서, CRM에 올리기 전에 아래를 먼저 확인하세요.

원본 7행(헤더 제외)에서 정확히 같은 행 2개를 지워 5행이 됐습니다. 칸 앞뒤 공백도 같이 정리됐습니다. 이 도구는 중복 제거와 공백 정리를 항상 함께 합니다. 원본은 그대로 두었습니다.

```
kim@example.com,Kim,Seoul
KIM@example.com,Kim,Seoul      ← kim@example.com과 대소문자만 다름
lee@example.com,Lee,busan      ← 아래 행과 city 대소문자만 다름
lee@example.com,Lee,Busan
park@example.com,Park,Incheon
```

- **남은 중복 후보:** 이 도구는 글자가 완전히 같은 행만 중복으로 봅니다. 그래서 `kim@`/`KIM@`과 `busan`/`Busan` 쌍이 그대로 남았습니다. 이메일은 보통 대소문자를 구분하지 않아서, CRM이 이메일로 고객을 식별하면 중복 등록되거나 오류가 날 수 있습니다.
- **정리 방법:** 이메일을 소문자로 통일하고 도시 표기를 맞춘 뒤 다시 중복을 제거하면 3행(kim, lee, park)이 됩니다. 원하시면 해드릴게요. 이 경우 어느 쪽 표기를 남길지(예: `Busan`)는 정해주세요.
- **확인 한계:** 행 수를 세는 명령은 이 환경에서 막혀서 파일을 직접 열어 확인했습니다.