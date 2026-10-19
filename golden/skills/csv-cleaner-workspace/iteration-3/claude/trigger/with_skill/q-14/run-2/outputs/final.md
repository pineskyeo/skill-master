```python
def strip_all(strings: list[str]) -> list[str]:
    """Return a new list with leading/trailing whitespace removed from each string."""
    return [s.strip() for s in strings]
```

Example:

```python
>>> strip_all(["  hello ", "world\n", "\tfoo\t"])
['hello', 'world', 'foo']
```

This returns a new list and leaves the original unchanged. To modify the list in place, use `strings[:] = [s.strip() for s in strings]`. If the list may contain non-string items, use `s.strip() if isinstance(s, str) else s`.