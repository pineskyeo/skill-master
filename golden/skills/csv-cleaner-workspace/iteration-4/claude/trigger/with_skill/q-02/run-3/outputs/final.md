`sales_q3.csv`는 아직 건드리지 않았습니다. 진행 전에 두 가지를 확인하고 싶습니다.

1. **파일 위치**: 작업 디렉터리(`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-zjtq6s6f/work`)에서 `sales_q3.csv`를 찾지 못했습니다. 다른 경로에 있다면 전체 경로를 알려주세요. 이 환경에서는 Bash가 `clean.py` 실행만 허용돼서 디렉터리 목록은 확인하지 못했습니다.

2. **중복 행 처리**: 쓸 수 있는 `csv-cleaner` 스킬은 공백을 지우면서 **중복 행도 함께 삭제**합니다. 둘을 따로 선택할 수 없습니다. 매출 데이터는 같은 내용의 행이 실제 거래일 수 있어서, 중복이 지워지면 합계가 달라질 수 있습니다.
   - **중복 삭제도 괜찮다면** 스킬로 정리해서 새 파일(예: `sales_q3.clean.csv`)에 저장하겠습니다. 원본은 덮어쓰지 않습니다.
   - **공백만 지우고 싶다면** 스킬은 쓰지 않는 게 맞습니다. 아래처럼 pandas로 직접 처리하면 됩니다.

```python
import pandas as pd

df = pd.read_csv("sales_q3.csv", skipinitialspace=True)
df.columns = df.columns.str.strip()
str_cols = df.select_dtypes(include="object").columns
df[str_cols] = df[str_cols].apply(lambda s: s.str.strip())
df.to_csv("sales_q3.stripped.csv", index=False)
```

어느 쪽으로 할지, 파일 경로는 어디인지 알려주세요.