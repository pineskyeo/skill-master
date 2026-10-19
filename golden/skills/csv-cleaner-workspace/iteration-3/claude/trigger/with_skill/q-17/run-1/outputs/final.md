products.csv의 빈 price 값을 0으로 채우지 못했습니다. 이 환경에서는 파일을 찾지도, 수정하지도 못했습니다.

- **파일 없음:** 작업 디렉터리(`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-sq9ektcs/work`)에 `products.csv`가 없습니다. Read로 열어 보니 "File does not exist"가 나왔습니다.
- **셸 제한:** Bash는 `clean.py`를 경로 두 개와 함께 실행하는 명령만 허용합니다. 그래서 `ls`나 `find`로 다른 위치를 찾아볼 수 없었고, 파일을 수정하는 명령도 실행할 수 없습니다.
- **스킬 불일치:** 쓸 수 있는 `csv-cleaner` 스킬은 중복 행 제거와 공백 제거만 합니다. 빈 값 채우기는 하지 않으므로 이 작업에 쓰면 요청과 다른 결과가 나옵니다. 그래서 실행하지 않았습니다.

다음 중 하나를 알려 주세요.

1. `products.csv`의 정확한 경로를 알려 주세요. 파일이 다른 곳에 있다면 그 경로로 읽어 보겠습니다.
2. 파일을 작업 디렉터리로 옮긴 뒤 다시 요청해 주세요.
3. 직접 처리하려면 아래 명령을 쓰세요. price 열이 있는 일반적인 CSV라고 가정한 명령입니다.
   ```
   python3 -c "import pandas as pd; df=pd.read_csv('products.csv'); df['price']=df['price'].fillna(0); df.to_csv('products.csv', index=False)"
   ```
   원본을 덮어쓰므로 먼저 백업해 두세요.