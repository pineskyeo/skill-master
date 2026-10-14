---
name: korean-proofreader
description: 한국어 문서의 맞춤법과 띄어쓰기를 교정한다. 사용자가 한국어 글의 교정을 요청할 때 사용한다. Use when the user asks to proofread Korean text.
license: MIT
metadata:
  version: 1.0.0
---
# 한국어 교정
## Inputs
- text: 교정할 한국어 문장 또는 문서
## Outputs
- corrected_text, changes(바뀐 부분 목록)
## Example
입력: "할수 있읍니다" → 출력: "할 수 있습니다" (띄어쓰기 1, 맞춤법 1)
