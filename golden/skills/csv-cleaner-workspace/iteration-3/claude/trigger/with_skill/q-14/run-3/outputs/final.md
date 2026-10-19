```python
def strip_all(strings):
    """Return a new list with leading and trailing whitespace removed from each string."""
    return [s.strip() for s in strings]
```

Example:

```python
>>> strip_all(["  hello ", "world\n", "\tfoo bar  "])
['hello', 'world', 'foo bar']
```

This returns a new list and leaves the original unchanged. To modify the list in place, use `strings[:] = [s.strip() for s in strings]`. If the list might hold non-string items, filter them with `s.strip() if isinstance(s, str) else s`.