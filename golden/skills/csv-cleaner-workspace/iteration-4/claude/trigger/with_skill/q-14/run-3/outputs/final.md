```python
def strip_all(strings: list[str]) -> list[str]:
    """Return a new list with leading/trailing whitespace removed from each string."""
    return [s.strip() for s in strings]
```

Example:

```python
>>> strip_all(["  hello ", "world\n", "\tfoo bar  "])
['hello', 'world', 'foo bar']
```

Notes:
- It returns a new list and leaves the original unchanged. To modify the list in place, use `strings[:] = [s.strip() for s in strings]`.
- `str.strip()` removes whitespace only from the ends of each string. Whitespace inside a string stays as it is.
- It assumes every item is a `str`. If the list might hold other types, use `s.strip() if isinstance(s, str) else s`.