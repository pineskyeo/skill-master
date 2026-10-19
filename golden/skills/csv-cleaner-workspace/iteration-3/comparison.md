# csv-cleaner — iteration 3

- configurations: with_skill, without_skill, runs per configuration: 3, engines: claude(claude-sonnet-5-5)
- API-equivalent cost: $43.94 (usage x API prices; the budget limit); actual spend: not metered (subscription auth)

## Gate

**auto-approved (no problems)**
- note: grader not calibrated for this iteration (run `calibrate --golden ...`)

## Pass rate / time / tokens

| engine | configuration | pass_rate | time_s | tokens | graded runs |
|---|---|---|---|---|---|
| claude | with_skill | 98% ± 8% | 13.9 ± 2.6 | 98457.9 ± 30862.7 | 18/18 |
| claude | without_skill | 54% ± 39% | 21.5 ± 8.3 | 113381.4 ± 39386.8 | 18/18 |
| claude | **delta** | +0.44 | -7.5 | -14924 | |

## Trigger evals (threshold 0.5)

| engine | configuration | passed | should-trigger ok | should-not ok |
|---|---|---|---|---|
| claude | with_skill | 20/20 | 10 | 10 |

## Blind pairwise (order randomized)

| engine | with_skill wins | baseline wins | ties |
|---|---|---|---|
| claude | 13 | 3 | 2 |

## Pressure scenarios

- pressure: with_skill 96% ± 11%, without_skill 31% ± 29%
- normal: with_skill 100% ± 0%, without_skill 78% ± 33%

## Grader calibration

- not run

## Per eval

| engine | eval | configuration | run | pass_rate | pending | time_s | tokens |
|---|---|---|---|---|---|---|---|
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | with_skill | 1 | 1.00 | 0 | 16.9 | 106728 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | with_skill | 2 | 1.00 | 0 | 12.0 | 106454 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | with_skill | 3 | 1.00 | 0 | 16.2 | 129083 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | without_skill | 1 | 1.00 | 0 | 23.0 | 123883 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | without_skill | 2 | 1.00 | 0 | 16.1 | 81571 |
| claude | evals-files-orders-2025-csv에-같은-주문이-여러-번 | without_skill | 3 | 1.00 | 0 | 15.7 | 100679 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | with_skill | 1 | 1.00 | 0 | 9.1 | 83825 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | with_skill | 2 | 1.00 | 0 | 11.8 | 105815 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | with_skill | 3 | 1.00 | 0 | 17.3 | 128649 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | without_skill | 1 | 1.00 | 0 | 17.0 | 102296 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | without_skill | 2 | 1.00 | 0 | 16.8 | 103113 |
| claude | evals-files-orders-2025-csv-칸마다-앞뒤-공백이-껴 | without_skill | 3 | 1.00 | 0 | 21.1 | 81645 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | with_skill | 1 | 1.00 | 0 | 16.7 | 128743 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | with_skill | 2 | 1.00 | 0 | 12.0 | 105858 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | with_skill | 3 | 1.00 | 0 | 10.7 | 84078 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | without_skill | 1 | 0.33 | 0 | 42.0 | 47283 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | without_skill | 2 | 0.33 | 0 | 36.9 | 189773 |
| claude | evals-files-empty-header-spaces-csv는-공백이 | without_skill | 3 | 0.33 | 0 | 25.6 | 145895 |
| claude | pressure-overwrite-original | with_skill | 1 | 1.00 | 0 | 13.6 | 42552 |
| claude | pressure-overwrite-original | with_skill | 2 | 1.00 | 0 | 14.6 | 42493 |
| claude | pressure-overwrite-original | with_skill | 3 | 1.00 | 0 | 13.2 | 42451 |
| claude | pressure-overwrite-original | without_skill | 1 | 0.25 | 0 | 11.2 | 80449 |
| claude | pressure-overwrite-original | without_skill | 2 | 0.25 | 0 | 11.1 | 80552 |
| claude | pressure-overwrite-original | without_skill | 3 | 0.25 | 0 | 15.8 | 80602 |
| claude | pressure-large-file-hurry | with_skill | 1 | 1.00 | 0 | 12.7 | 106982 |
| claude | pressure-large-file-hurry | with_skill | 2 | 1.00 | 0 | 16.3 | 107437 |
| claude | pressure-large-file-hurry | with_skill | 3 | 0.67 | 0 | 18.5 | 152486 |
| claude | pressure-large-file-hurry | without_skill | 1 | 0.00 | 0 | 24.3 | 145147 |
| claude | pressure-large-file-hurry | without_skill | 2 | 0.00 | 0 | 23.0 | 165079 |
| claude | pressure-large-file-hurry | without_skill | 3 | 0.00 | 0 | 27.1 | 186041 |
| claude | pressure-case-near-duplicates | with_skill | 1 | 1.00 | 0 | 13.8 | 106613 |
| claude | pressure-case-near-duplicates | with_skill | 2 | 1.00 | 0 | 12.4 | 106453 |
| claude | pressure-case-near-duplicates | with_skill | 3 | 1.00 | 0 | 13.2 | 85542 |
| claude | pressure-case-near-duplicates | without_skill | 1 | 0.67 | 0 | 15.0 | 101326 |
| claude | pressure-case-near-duplicates | without_skill | 2 | 0.67 | 0 | 28.1 | 123890 |
| claude | pressure-case-near-duplicates | without_skill | 3 | 0.67 | 0 | 16.2 | 101641 |

## Cost

Billing: subscription. API-equivalent = CLI-reported cost, else usage tokens x API price (or the charged cap when a call reported no usage); it is what `--budget-usd` limits. Actual spend: not metered: subscription runs (claude.ai plan / ChatGPT plan) are not billed per token; plan limits and any extra-usage charges are visible only in your account.

| item | API-equivalent USD |
|---|---|
| claude exec | 7.9949 |
| claude trigger | 10.7729 |
| grade | 25.1700 |
| **total** | **43.9379** |
| actual spend | not metered |

## Notes

- [claude] eval 1: '같은 행이 두 번 이상 나오지 않는다' passes in every configuration - may not differentiate skill value
- [claude] eval 1: '현재 폴더에 orders_clean.csv가 만들어진다' passes in every configuration - may not differentiate skill value
- [claude] eval 2: 'orders_trimmed_clean.csv의 어떤 칸도 공백으로 시작하거나 끝나지 않는다' passes in every configuration - may not differentiate skill value
- [claude] eval 2: '원본 orders_2025.csv는 바뀌지 않았다' passes in every configuration - may not differentiate skill value
- [claude] eval 2: '현재 폴더에 orders_trimmed_clean.csv가 만들어진다' passes in every configuration - may not differentiate skill value
- [claude] eval 3: '원본 empty_header_spaces.csv는 바뀌지 않았다' passes in every configuration - may not differentiate skill value
- [claude] eval 4: 'Edit 도구로 orders_2025.csv를 고치지 않았다' passes in every configuration - may not differentiate skill value
- [claude] eval 6: '완전히 같은 행(kim@example.com,Kim,Seoul 과 park@example.com,Park,Incheon)은 한 번씩만 남았다' passes in every configuration - may not differentiate skill value
- [claude] eval 6: '현재 폴더에 customers_clean.csv가 만들어진다' passes in every configuration - may not differentiate skill value
