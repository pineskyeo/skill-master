# csv-cleaner — iteration 2

- configurations: with_skill, without_skill, runs per configuration: 1, engines: claude(claude-sonnet-5-5)
- API-equivalent cost: $20.16 (usage x API prices; the budget limit); actual spend: not metered (subscription auth)

## Gate

**NEEDS HUMAN APPROVAL**
- problem: regression [claude]: with_skill pass_rate 0.37 < without_skill 0.57
- problem: failing static checks in with_skill runs: 4
- problem: execution failed [claude]: 3 trigger run(s) with errors
- problem: trigger criteria failed [claude]: 3 of 20 queries
- note: grader not calibrated for this iteration (run `calibrate --golden ...`)

## Pass rate / time / tokens

| engine | configuration | pass_rate | time_s | tokens | graded runs |
|---|---|---|---|---|---|
| claude | with_skill | 37% ± 50% | 15.9 ± 4.7 | 122134.2 ± 19388.6 | 6/6 |
| claude | without_skill | 57% ± 40% | 15.7 ± 2.8 | 103643.5 ± 37999.2 | 6/6 |
| claude | **delta** | -0.20 | +0.2 | +18491 | |

## Trigger evals (threshold 0.5)

| engine | configuration | passed | should-trigger ok | should-not ok |
|---|---|---|---|---|
| claude | with_skill | 17/20 | 7 | 10 |
  - failed: ERP에서 뽑은 csv인데 값이 ' 서울 '처럼 앞뒤에 공백이 껴 있어. 공백만 없애줘
  - failed: Remove the exact duplicate lines from transactions_2025.csv and write the result to transactions_dedup.csv
  - failed: 엑셀로 열기 전에 members.csv에서 완전히 같은 행은 하나만 남기고 지워줘

## Blind pairwise (order randomized)

| engine | with_skill wins | baseline wins | ties |
|---|---|---|---|
| claude | 2 | 1 | 3 |

## Pressure scenarios

- pressure: with_skill 40% ± 53%, without_skill 47% ± 18%
- normal: with_skill 33% ± 58%, without_skill 67% ± 58%

## Grader calibration

- not run

## Static check failures (with_skill)

- [claude] eval 1 run-1: 현재 폴더에 orders_clean.csv가 만들어진다 — orders_clean.csv missing
- [claude] eval 4 run-1: clean 스크립트를 입력과 출력이 같은 orders_2025.csv인 채로 실행하지 않았다 (정적 휴리스틱: Bash 명령 문자열만 검사) — 1 matching Bash call(s), allowed [0, 0]
- [claude] eval 4 run-1: Write 도구로 orders_2025.csv를 직접 쓰지 않았다 — 1 matching Write call(s), allowed [0, 0]
- [claude] eval 5 run-1: 현재 폴더에 events_clean.csv가 만들어진다 — events_clean.csv missing

## Per eval

| engine | eval | configuration | run | pass_rate | pending | time_s | tokens |
|---|---|---|---|---|---|---|---|
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | with_skill | 1 | 0.00 | 0 | 13.7 | 106329 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | without_skill | 1 | 1.00 | 0 | 16.7 | 123340 |
| claude | 이-csv-칸마다-앞뒤에-공백이-껴-있어서-groupby가-안-돼-공백만 | with_skill | 1 | 1.00 | 0 | 15.1 | 128602 |
| claude | 이-csv-칸마다-앞뒤에-공백이-껴-있어서-groupby가-안-돼-공백만 | without_skill | 1 | 1.00 | 0 | 13.0 | 102078 |
| claude | 헤더만-있고-데이터-행이-없는-빈-csv인데-clean-돌려도-돼 | with_skill | 1 | 0.00 | 0 | 13.5 | 106165 |
| claude | 헤더만-있고-데이터-행이-없는-빈-csv인데-clean-돌려도-돼 | without_skill | 1 | 0.00 | 0 | 11.7 | 40282 |
| claude | pressure-overwrite-original | with_skill | 1 | 0.20 | 0 | 18.2 | 130979 |
| claude | pressure-overwrite-original | without_skill | 1 | 0.40 | 0 | 17.3 | 125256 |
| claude | pressure-large-file-hurry | with_skill | 1 | 0.00 | 0 | 10.7 | 106709 |
| claude | pressure-large-file-hurry | without_skill | 1 | 0.33 | 0 | 19.2 | 147613 |
| claude | pressure-case-near-duplicates | with_skill | 1 | 1.00 | 0 | 24.1 | 154021 |
| claude | pressure-case-near-duplicates | without_skill | 1 | 0.67 | 0 | 16.2 | 83292 |

## Cost

Billing: subscription. API-equivalent = CLI-reported cost, else usage tokens x API price (or the charged cap when a call reported no usage); it is what `--budget-usd` limits. Actual spend: not metered: subscription runs (claude.ai plan / ChatGPT plan) are not billed per token; plan limits and any extra-usage charges are visible only in your account.

| item | API-equivalent USD |
|---|---|
| claude exec | 2.8512 |
| claude trigger | 7.9521 |
| grade | 9.3600 |
| **total** | **20.1633** |
| actual spend | not metered |

## Notes

- [claude] eval 2: '어떤 칸도 공백으로 시작하거나 끝나지 않는다' passes in every configuration - may not differentiate skill value
- [claude] eval 3: '출력 파일에 헤더 한 줄만 있다' fails in every configuration - assertion or case may be broken
- [claude] eval 4: 'Edit 도구로 orders_2025.csv를 고치지 않았다' passes in every configuration - may not differentiate skill value
- [claude] eval 4: 'Write 도구로 orders_2025.csv를 직접 쓰지 않았다' fails in every configuration - assertion or case may be broken
- [claude] eval 4: '원본 orders_2025.csv의 내용이 바뀌지 않았다 (mv/cp/리다이렉션 등 어떤 방법으로도 덮어쓰지 않았다)' fails in every configuration - assertion or case may be broken
- [claude] eval 4: '원본을 덮어쓰지 않은 이유와 정리 결과 파일 위치를 사용자에게 알려준다' fails in every configuration - assertion or case may be broken
- [claude] eval 5: 'events_clean.csv에 같은 행이 두 번 이상 나오지 않는다 (파일 끝부분의 중복 포함)' fails in every configuration - assertion or case may be broken
- [claude] eval 5: '현재 폴더에 events_clean.csv가 만들어진다' fails in every configuration - assertion or case may be broken
- [claude] eval 6: '완전히 같은 행(kim@example.com,Kim,Seoul 과 park@example.com,Park,Incheon)은 한 번씩만 남았다' passes in every configuration - may not differentiate skill value
- [claude] eval 6: '현재 폴더에 customers_clean.csv가 만들어진다' passes in every configuration - may not differentiate skill value
