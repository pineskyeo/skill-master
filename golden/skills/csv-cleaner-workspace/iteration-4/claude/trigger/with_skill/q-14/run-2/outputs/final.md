```python
def strip_all(strings: list[str]) -> list[str]:
    """Return a new list with leading/trailing whitespace removed from each string."""
    return [s.strip() for s in strings]
```

Example:

```python
>>> strip_all(["  hello ", "\tworld\n", "no_spaces"])
['hello', 'world', 'no_spaces']
```

This returns a new list and leaves the original unchanged. To modify the list in place instead:

```python
def strip_all_inplace(strings: list[str]) -> None:
    strings[:] = [s.strip() for s in strings]
```

`str.strip()` removes all leading and trailing whitespace, including spaces, tabs and newlines. It does not touch whitespace inside a string. If the list may hold non-string items, add a type check, such as `s.strip() if isinstance(s, str) else s`.