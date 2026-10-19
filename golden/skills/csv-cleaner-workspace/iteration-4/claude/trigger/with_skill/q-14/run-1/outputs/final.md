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

This returns a new list and leaves the original unchanged. If you'd rather modify the list in place, use:

```python
def strip_all_inplace(strings: list[str]) -> None:
    strings[:] = [s.strip() for s in strings]
```

If the list might hold non-string items, you could change the comprehension to `s.strip() if isinstance(s, str) else s`.