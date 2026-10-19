`sales_q3.csv`는 아직 건드리지 않았고, 아래 두 가지를 확인해 주셔야 진행할 수 있어요.

1. **파일을 찾지 못했어요.** 작업 디렉터리(`.../skill-eval-claude-ypn_tpmt/work`)에 `sales_q3.csv`가 없다고 나옵니다. 이 환경에서는 Bash가 `clean.py` 실행만 허용돼서 `ls`나 `find`로 다른 위치를 찾아볼 수도 없었어요. 파일의 정확한 경로를 알려 주시거나 작업 디렉터리로 옮겨 주세요.

2. **사용 가능한 `csv-cleaner` 스킬은 공백 제거와 함께 중복 행도 지워요.** 공백만 따로 정리하는 옵션이 없습니다. 매출 데이터에서는 내용이 같은 행도 실제로 별개의 거래일 수 있어서, 중복 제거까지 하면 합계가 달라질 수 있어요. 어느 쪽으로 할지 알려 주세요.
   - **(a)** 공백 제거와 중복 제거를 둘 다 하기. 이 경우 스킬을 그대로 씁니다.
   - **(b)** 공백만 제거하기. 이 경우 스킬을 쓰지 않고 별도로 처리해야 하는데, 지금은 Bash 제한 때문에 다른 방법이 막혀 있어요.

어느 쪽이든 결과는 새 파일(예: `sales_q3.clean.csv`)로 저장하고 원본은 덮어쓰지 않아요.

참고로 pandas에서 바로 처리하려면 아래처럼 하면 됩니다.

```python
df = pd.read_csv("sales_q3.csv", skipinitialspace=True)
df.columns = df.columns.str.strip()
df = df.apply(lambda c: c.str.strip() if c.dtype == "object" else c)
```