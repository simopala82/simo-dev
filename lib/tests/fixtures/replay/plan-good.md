# Sample Implementation Plan

**Goal:** add a helper.

## Global Constraints
- none
---

### Task 1: Helper

**Files:**
- Modify: `src/app.py:1-1`
- Create: `src/helper.py`
- Test: `tests/test_helper.py`

- [ ] **Step 1: Write the failing test**

```python
from src.helper import double

def test_double():
    assert double(2) == 4
```

- [ ] **Step 2: Run** `pytest $W/tests/test_helper.py -q`

```python
    def double(x):
        return x * 2
```

Also run `pytest <worktree-abs>/tests/test_app.py`.

Also run `pytest /elsewhere/wt/tests/test_app.py`.

```python
    value = compute()
    return value
```

### Task 2: Helper again

**Files:**
- Modify: `src/helper.py`
