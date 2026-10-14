---
name: no-context-agent
description: Looks up one word in a dictionary. Use when the user asks what a word means.
goal: 단어 하나의 뜻을 사전 근거와 함께 한 문단으로 설명한다.
tools: [dict_lookup]
success_criteria:
  - 사전 출처 1개 이상 인용
---
# Dictionary Lookup
## Tools
- dict_lookup: 단어 뜻 조회
