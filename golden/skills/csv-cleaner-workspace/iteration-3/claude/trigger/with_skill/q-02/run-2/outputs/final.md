sales_q3.csv의 공백을 정리하기 전에 하나만 확인할게요. 아직 파일은 건드리지 않았습니다.

사용할 수 있는 csv-cleaner 스킬은 칸 앞뒤 공백 제거와 중복 행 제거를 항상 같이 합니다. 공백만 따로 정리하는 옵션이 없습니다. 중복 행 제거는 공백을 다듬은 뒤에 이루어지기 때문에, 공백 때문에 달랐던 행이 같은 행으로 합쳐져 사라질 수도 있습니다.

매출 데이터에서는 내용이 같은 행이 실제로 따로 발생한 거래일 수 있습니다. 그런 행이 지워지면 groupby 합계와 건수가 달라집니다.

어느 쪽으로 할까요?

1. **중복 제거까지 괜찮다**: 스킬로 바로 처리합니다. 원본은 그대로 두고 `sales_q3.clean.csv` 같은 새 파일로 저장합니다.
2. **공백만 정리하고 싶다**: 이 스킬은 쓸 수 없습니다. 대신 pandas 한 줄로 정리할 수 있습니다. 지금은 Bash가 이 스킬의 스크립트만 실행하도록 제한되어 있어서, 그 코드를 직접 돌리지 않고 드리기만 할 수 있습니다.

   ```python
   import pandas as pd
   df = pd.read_csv("sales_q3.csv", skipinitialspace=True)
   df.columns = df.columns.str.strip()
   obj = df.select_dtypes(include="object").columns
   df[obj] = df[obj].apply(lambda s: s.str.strip())
   df.to_csv("sales_q3.trimmed.csv", index=False)
   ```

어느 쪽인지 알려주시면 이어서 진행할게요.