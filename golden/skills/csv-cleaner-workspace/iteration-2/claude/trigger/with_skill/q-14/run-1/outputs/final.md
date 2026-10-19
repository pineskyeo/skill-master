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

Notes:
- This returns a new list and leaves the original untouched. To modify in place, use `strings[:] = [s.strip() for s in strings]`.
- Use `lstrip()` or `rstrip()` instead of `strip()` if you only want one side trimmed.
- If the list may contain non-strings, guard with `s.strip() if isinstance(s, str) else s`.