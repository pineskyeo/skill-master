# csv-cleaner — iteration 4

- configurations: with_skill, without_skill, runs per configuration: 3, engines: claude(claude-sonnet-5-5)
- API-equivalent cost: $35.80 (usage x API prices; the budget limit); actual spend: not metered (subscription auth)

## Gate

**NEEDS HUMAN APPROVAL**
- problem: regression [claude]: with_skill pass_rate 0.94 < previous iteration 0.98
- problem: failing static checks in with_skill runs: 2

## Pass rate / time / tokens

| engine | configuration | pass_rate | time_s | tokens | graded runs |
|---|---|---|---|---|---|
| claude | with_skill | 94% ± 24% | 19.3 ± 4.4 | 98072.1 ± 28086.6 | 18/18 |
| claude | without_skill | 54% ± 39% | 25.4 ± 11.3 | 114685.8 ± 35963.8 | 18/18 |
| claude | **delta** | +0.40 | -6.2 | -16614 | |

## Trigger evals (threshold 0.5)

| engine | configuration | passed | should-trigger ok | should-not ok |
|---|---|---|---|---|
| claude | with_skill | 20/20 | 10 | 10 |

## Blind pairwise (order randomized)

| engine | with_skill wins | baseline wins | ties |
|---|---|---|---|
| claude | 14 | 3 | 1 |

## Pressure scenarios

- pressure: with_skill 100% ± 0%, without_skill 31% ± 29%
- normal: with_skill 89% ± 33%, without_skill 78% ± 33%

## Grader calibration

- agreement 1.00, kappa 1.00 on 6 golden examples (threshold 0.80), grader codex(CLI default model)

## Static check failures (with_skill)

- [claude] eval 1 run-3: 현재 폴더에 orders_clean.csv가 만들어진다 — orders_clean.csv missing
- [claude] eval 1 run-3: 같은 행이 두 번 이상 나오지 않는다 (orders_clean.csv: 셀 공백 무시 기준 중복 없음, 원본의 서로 다른 행 3개가 각각 남음) — orders_clean.csv missing

## Per eval

| engine | eval | configuration | run | pass_rate | pending | time_s | tokens |
|---|---|---|---|---|---|---|---|
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | with_skill | 1 | 1.00 | 0 | 21.7 | 107886 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | with_skill | 2 | 1.00 | 0 | 15.5 | 107691 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | with_skill | 3 | 0.00 | 0 | 24.2 | 85654 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | without_skill | 1 | 1.00 | 0 | 26.8 | 102474 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | without_skill | 2 | 1.00 | 0 | 16.8 | 101708 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | without_skill | 3 | 1.00 | 0 | 25.2 | 81980 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | with_skill | 1 | 1.00 | 0 | 15.4 | 106984 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | with_skill | 2 | 1.00 | 0 | 22.7 | 128865 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | with_skill | 3 | 1.00 | 0 | 28.9 | 129470 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | without_skill | 1 | 1.00 | 0 | 16.8 | 81841 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | without_skill | 2 | 1.00 | 0 | 34.1 | 146020 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | without_skill | 3 | 1.00 | 0 | 19.8 | 82111 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | with_skill | 1 | 1.00 | 0 | 23.0 | 106994 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | with_skill | 2 | 1.00 | 0 | 15.3 | 85340 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | with_skill | 3 | 1.00 | 0 | 21.9 | 106957 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | without_skill | 1 | 0.33 | 0 | 30.6 | 165981 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | without_skill | 2 | 0.33 | 0 | 23.9 | 166392 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | without_skill | 3 | 0.33 | 0 | 62.4 | 125180 |
| claude | pressure-overwrite-original | with_skill | 1 | 1.00 | 0 | 13.7 | 42595 |
| claude | pressure-overwrite-original | with_skill | 2 | 1.00 | 0 | 14.2 | 42762 |
| claude | pressure-overwrite-original | with_skill | 3 | 1.00 | 0 | 13.4 | 42648 |
| claude | pressure-overwrite-original | without_skill | 1 | 0.25 | 0 | 28.7 | 81304 |
| claude | pressure-overwrite-original | without_skill | 2 | 0.25 | 0 | 12.8 | 80962 |
| claude | pressure-overwrite-original | without_skill | 3 | 0.25 | 0 | 14.9 | 81065 |
| claude | pressure-large-file-hurry | with_skill | 1 | 1.00 | 0 | 15.8 | 108137 |
| claude | pressure-large-file-hurry | with_skill | 2 | 1.00 | 0 | 22.2 | 108298 |
| claude | pressure-large-file-hurry | with_skill | 3 | 1.00 | 0 | 19.7 | 129587 |
| claude | pressure-large-file-hurry | without_skill | 1 | 0.00 | 0 | 28.9 | 167440 |
| claude | pressure-large-file-hurry | without_skill | 2 | 0.00 | 0 | 21.0 | 166635 |
| claude | pressure-large-file-hurry | without_skill | 3 | 0.00 | 0 | 28.6 | 144415 |
| claude | pressure-case-near-duplicates | with_skill | 1 | 1.00 | 0 | 17.4 | 108265 |
| claude | pressure-case-near-duplicates | with_skill | 2 | 1.00 | 0 | 22.0 | 109218 |
| claude | pressure-case-near-duplicates | with_skill | 3 | 1.00 | 0 | 19.8 | 107947 |
| claude | pressure-case-near-duplicates | without_skill | 1 | 0.67 | 0 | 32.3 | 125713 |
| claude | pressure-case-near-duplicates | without_skill | 2 | 0.67 | 0 | 13.5 | 81135 |
| claude | pressure-case-near-duplicates | without_skill | 3 | 0.67 | 0 | 21.0 | 81988 |

## Cost

Billing: subscription. API-equivalent = CLI-reported cost, else usage tokens x API price (or the charged cap when a call reported no usage); it is what `--budget-usd` limits. Actual spend: not metered: subscription runs (claude.ai plan / ChatGPT plan) are not billed per token; plan limits and any extra-usage charges are visible only in your account.

| item | API-equivalent USD |
|---|---|
| claude exec | 8.0292 |
| claude trigger | 11.2134 |
| calibrate | 2.8800 |
| grade | 13.6800 |
| **total** | **35.8026** |
| actual spend | not metered |

## Notes

- [claude] eval 2: 'orders_trimmed_clean.csv는 원본의 칸 공백을 정리하고 완전히 같은 행을 지운 결과와 같다 (헤더 + 3행; 공백으로 시작·끝나는 칸 없음)' passes in every configuration - may not differentiate skill value
- [claude] eval 2: '원본 orders_2025.csv는 바뀌지 않았다' passes in every configuration - may not differentiate skill value
- [claude] eval 2: '현재 폴더에 orders_trimmed_clean.csv가 만들어진다' passes in every configuration - may not differentiate skill value
- [claude] eval 3: '원본 empty_header_spaces.csv는 바뀌지 않았다' passes in every configuration - may not differentiate skill value
- [claude] eval 4: 'Edit 도구로 orders_2025.csv를 고치지 않았다' passes in every configuration - may not differentiate skill value
- [claude] eval 6: '완전히 같은 행(kim@example.com,Kim,Seoul 과 park@example.com,Park,Incheon)은 한 번씩만 남았다' passes in every configuration - may not differentiate skill value
- [claude] eval 6: '현재 폴더에 customers_clean.csv가 만들어진다' passes in every configuration - may not differentiate skill value
- [claude] eval 1 with_skill: high variance (67% ± 58%) - flaky or ambiguous instructions
