# Day 17 — Testing with pytest

> **Goal:** learn the craft — fixtures, isolation, parametrize, mocking, and the
> judgement calls: what to test, what not to, and why coverage is not quality.
> **Time:** ~2 hours · **Port:** 5017 · **Builds on:** Day 14

---

## 1. Why this matters

Day 14 shipped a test suite. Today the **application is deliberately small** so
that `tests/` is the subject.

The question a good test answers is not *"did this line run?"* but:

> **Would this test fail if the behaviour were wrong?**

Section 8 proves the difference by breaking the code on purpose and seeing which
tests notice.

## 2. What you will build

A meeting-room booking system with one genuinely tricky rule — interval overlap
— and one external dependency to mock.

```
17_testing_with_pytest/
├── pytest.ini
├── bookings/
│   ├── models.py      overlaps() ← a pure function, exhaustively testable
│   ├── services.py    business rules, no HTTP
│   └── __init__.py    thin views: parse, delegate, choose a status code
└── tests/
    ├── conftest.py                    fixtures
    ├── test_overlap.py                pure logic — fastest, most valuable
    ├── test_booking_rules.py          rules via the service layer
    ├── test_api.py                    HTTP — fewest, slowest
    ├── test_holidays.py               mocking an external service
    └── test_what_coverage_misses.py   the judgement calls
```

## 3. Run it

```bash
source .venv/bin/activate
cd 17_testing_with_pytest

pytest                       # 45 tests
pytest -v                    # one line per test
pytest tests/test_overlap.py -v
pytest -k "conflict"         # select by name
pytest -m integration        # select by marker
pytest -m "not slow"
pytest --cov=bookings --cov-report=term-missing
pytest -x --lf               # stop at first failure; rerun last failures
```

## 4. The pyramid, concretely

| Layer | File | Needs | Speed | Count |
|---|---|---|---|---|
| **Pure functions** | `test_overlap.py` | nothing | µs | many |
| **Services** | `test_booking_rules.py` | a database | ms | some |
| **HTTP** | `test_api.py` | app + client | ms+ | few |

The rules are tested **once**, at the service layer. `test_api.py` only checks
what solely an HTTP test can prove: status codes, JSON shape, wiring. Re-testing
every rule through the client would be slower and would tell you nothing new.

> **If a rule is hard to test, it is usually tangled with I/O.** Extracting it
> improves the code, not just the tests.

## 5. Fixtures

```python
def test_x(client): ...     # pytest sees `client`, builds it, injects it
client → app                # `client` itself asks for `app`
```

Anything before `yield` is set-up; anything after is tear-down, and it runs
**even when the test fails**.

### Scope

| Scope | Rebuilt | Trade-off |
|---|---|---|
| `function` *(default)* | every test | slowest, safest |
| `module` | per file | shared state within a file |
| `session` | once per run | fastest, riskiest |

**Default to `function`.** A suite where tests can affect one another produces
order-dependent failures, and those cost hours. There is an explicit test for
this — `test_each_test_starts_with_a_clean_database`.

### Compose small fixtures

```python
rooms → room → booked_room
```

Each test asks for exactly what it needs, and the signature documents the
preconditions. One giant `setup_everything` fixture does neither.

## 6. `parametrize`: a table of cases

```python
@pytest.mark.parametrize(("a_start","a_end","b_start","b_end","expected"), [
    pytest.param(9, 10, 11, 12, False, id="entirely_before"),
    pytest.param(9, 10, 10, 11, False, id="back_to_back_no_clash"),   # ← the boundary
    pytest.param(9, 10.5, 10, 11, True,  id="one_minute_overlap"),
])
def test_overlaps(...): ...
```

Each row is a **separate test** with its own pass/fail. A loop inside one test
stops at the first failure and reports one result; this reports "3 failed, 9
passed" and names them. The `id=` strings make `pytest -v` read as documentation
and let you re-run one case with `-k back_to_back`.

**The boundaries are the point.** `9-10` versus `10-11` is exactly where a `<=`
instead of a `<` silently blocks every back-to-back meeting in the building — a
bug that looks like nothing in review.

## 7. Time and external services: inject, don't wait

### The clock

```python
def create_booking(*, starts_at, ends_at, now: datetime | None = None):
    now = now or utcnow()
```

One optional parameter removes a whole class of flaky test. A test asserting
"a booking in the past is rejected" must not depend on when the suite runs, and
a test written on 31 December must still pass in January.

Every time-dependent test is anchored to a `FROZEN_NOW` constant in
`conftest.py`.

### The network

> **A test must never make a real network call.** It is slow, fails offline,
> fails when the third party has an incident, and may cost money.

```python
monkeypatch.setattr(requests, "get", fake_get)
```

`monkeypatch` restores the original automatically — even if the test fails.

**Patch where the name is *looked up*, not where it is defined.** `services.py`
does `import requests` and calls `requests.get`, so patching `requests.get`
works. Had it done `from requests import get`, you would patch
`bookings.services.get` instead. This is the single most common mocking
confusion.

And test the **failure paths** — that is the whole reason to mock:

```python
@pytest.mark.parametrize("failure", [requests.ConnectionError(...),
                                     requests.Timeout(...), ValueError(...)])
def test_degrades_gracefully(monkeypatch, failure): ...
```

There is also a test asserting the call **passes a timeout at all**, because
`requests` has no default one and a hung third party will hang your worker.

## 8. Coverage is not quality — proof

Two tests. **Identical coverage.**

```python
def test_100_percent_coverage_zero_value(...):
    result = create_booking(...)
    assert result is not None            # ← always true. Asserts nothing.

def test_the_same_path_with_a_real_assertion(...):
    result = create_booking(booked_by="  Ana  ", ...)
    assert result.booking.booked_by == "Ana"      # whitespace stripped
    assert result.booking.ends_at - result.booking.starts_at == timedelta(hours=1)
```

Now break the code on purpose:

```bash
# change `start_a < end_b` to `start_a <= end_b` in models.py, then:
pytest -q
```

```
FAILED tests/test_overlap.py::test_overlaps[back_to_back_no_clash]
FAILED tests/test_overlap.py::test_overlaps[back_to_back_reversed]
FAILED tests/test_what_coverage_misses.py::test_boundary_not_just_the_middle
```

The boundary tests caught it. The high-coverage-no-assertion test did not.
**That is what coverage cannot see.**

> This is manual **mutation testing**. Tools like `mutmut` automate it, and it
> is a far better measure of a suite than a coverage percentage.

### The experiment also found a real risk

Mutating `overlaps()` broke **no booking-level test** — because those go through
`find_conflict()`, which implements the same rule again **in SQL**. Duplicated
logic drifts.

So the suite gained a tripwire:

```python
def test_sql_and_python_overlap_rules_agree(...):
    for start, end in candidates:
        assert overlaps(...) == (find_conflict(...) is not None)
```

Mutating the SQL side now fails that test too. **A rule expressed in two places
needs a test that pins them together.**

## 9. What *not* to test

| Don't test | Because |
|---|---|
| SQLAlchemy saves a row | you are testing the library |
| Flask routes a URL to a view | you are testing the framework |
| a getter returns what a setter set | you are testing Python |
| the exact prose of a message | breaks on a reword, catches no bug |
| private helpers directly | cements the implementation; test the behaviour |
| that a constant equals its own value | tautology |

Note how `test_rejects_invalid_bookings` asserts on a **substring**
(`"seats 4"`), not the whole sentence: rewording for clarity does not break the
test, while the right rule is still proven to have fired.

**Test error paths harder than happy paths.** Everybody tests "it works". Far
fewer test "a client sent nonsense" — and that is what fills your error tracker
on launch day.

## 10. Best practices introduced today

| Practice | Reason |
|---|---|
| Pure logic in pure functions | fastest tests, no flakes, exhaustive |
| Business rules out of views | testable with no client or status code |
| Function-scoped fixtures | no cross-test contamination |
| Small composable fixtures | signatures document preconditions |
| `parametrize` over loops | per-case pass/fail with readable ids |
| Test boundaries, not middles | that is where off-by-one lives |
| Inject the clock | tests cannot rot |
| Anchor to a frozen constant | identical results in any month |
| Never touch the network | speed, determinism, cost |
| Patch where the name is looked up | the classic mocking mistake |
| Assert the timeout was passed | `requests` has no default |
| Test third-party failure paths | those are the ones that run in production |
| `--strict-markers` | a typo'd marker becomes an error, not a no-op |
| Declare markers in `pytest.ini` | discoverable and enforced |
| Substring assertions on prose | resilient to rewording |
| Return result objects, not exceptions | trivial to assert on |
| Tripwire tests for duplicated rules | SQL and Python drift |
| Occasional manual mutation | measures the suite, not the lines |

## 11. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Passes alone, fails in the suite | shared state | function-scoped fixtures |
| Passes today, fails next month | real clock | inject `now` |
| Slow suite | real network, or scrypt hashing | mock it; cheap hash in testing config |
| `fixture 'x' not found` | not in `conftest.py`, or a typo | check location and spelling |
| Marker silently ignored | no `--strict-markers` | add it |
| Mock has no effect | patched the wrong namespace | patch where it is *looked up* |
| Fails offline | a real API call | `monkeypatch` |
| 90% coverage, bugs everywhere | assertions too weak | mutate and see |
| Test breaks on a copy edit | asserted exact prose | assert a substring |
| `Working outside of application context` | fixture missing the context | `with app.app_context():` |
| Everything breaks after a refactor | tests coupled to internals | test behaviour, not structure |

## 12. Exercises

1. Add a `cancel_booking` service with rules (cannot cancel a past booking,
   cannot cancel someone else's) and test it **before** writing the view.
2. Break `create_booking` in three different ways and record which tests catch
   each. Fix the gaps.
3. Install `mutmut` and run it against `bookings/`. Compare its verdict with the
   coverage percentage.
4. Convert the `app` fixture to session scope with a per-test transaction
   rollback. Measure the speed-up, then note what `create_booking`'s `commit()`
   forces you to handle.
5. Add `pytest-randomly` so tests run in a random order. If anything breaks, you
   had a hidden dependency.
6. Add a `@pytest.mark.slow` test and confirm `-m "not slow"` skips it.
7. Use `hypothesis` to property-test `overlaps()` with generated intervals, and
   compare what it finds against the hand-written table.

## 13. What's next

**[Day 18 — Config, Logging and Errors →](../18_config_logging_and_errors/)**
Twelve-factor configuration with `pydantic-settings`, structured logging,
request ids, and error handling that tells you what actually happened.
