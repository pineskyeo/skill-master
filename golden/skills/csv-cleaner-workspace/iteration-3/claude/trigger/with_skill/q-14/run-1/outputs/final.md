```python
def strip_all(strings: list[str]) -> list[str]:
    """Return a new list with leading/trailing whitespace removed from each string."""
    return [s.strip() for s in strings]
```

Example:

```python
>>> strip_all(["  hello ", "\tworld\n", "no_change"])
['hello', 'world', 'no_change']
```

Notes:
- It returns a new list and leaves the original unchanged. For in-place modification, use `strings[:] = [s.strip() for s in strings]`.
- `str.strip()` removes all leading and trailing whitespace, including spaces, tabs and newlines. To strip only one side, use `lstrip()` or `rstrip()`.
- Every element must be a `str`. Non-string items, such as `None` or ints, will raise an `AttributeError`.