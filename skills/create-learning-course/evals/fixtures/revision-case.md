# Synthetic revision fixture

## Primary definition

A cache stores a result so a later request can reuse it instead of repeating
the original work. Reuse is correct only while the stored result remains valid.
For a calculation taking 8 units of work and a lookup taking 1, three requests
cost 24 units without reuse and 10 units with one calculation and two lookups.
This model excludes storage and invalidation work; it is not a measurement.

## Repeated definition

A cache stores a result so a later request can reuse it instead of repeating
the original work. Reuse is correct only while the stored result remains valid.

## Useful later refresher

Recall that reuse requires validity. If the input changes from 4 to 5 but the
cache key still identifies 4, a fast response can be incorrect. Invalidation
removes or marks a stale entry so later work recomputes it. This refresher
introduces the validity condition needed for the following exercise.

## Existing lab claim

Run the lab at sizes 4, 8 and 16 and compare results.

## Actual supplied code

```python
def run():
    values = [1, 2, 3, 4]
    cached = sum(values)
    assert cached == sum(values)
    return cached


if __name__ == "__main__":
    print(run())
```

The fixture is synthetic. The lab has no size argument, timing measurement,
persistent cache or invalidation implementation.
