# Working in this repo

## Every request is a refactor request

A feature request is information about how the problem has changed, not just a
thing to add. When it makes sense, first reshape the code so the new request
looks obvious in it, then add the feature on top of the reshaped code.

The failure mode to avoid is the opposite: finding the nearest conditional and
bolting the new behavior onto it. That is locally cheap and globally corrosive.

## Pin behavior with tests before refactoring

Before changing the shape of any code, write characterization tests that capture
its current behavior and watch them pass against the unchanged code. These are a
safety net against regressions, not tests of the new feature. They must stay
green through the entire refactor — a refactor that changes behavior is a bug,
not a refactor.

If you cannot make the current behavior observable enough to test, fix that
first: add a minimal seam that makes it observable rather than refactoring blind.

## Order of work for a change

1. **Name the real request** in one sentence — the concept it actually
   introduces or changes, not the surface ask. "Add chapter E" is really "the
   world now has phases." "Add a discount" is really "prices are no longer
   fixed."
2. **Pin current behavior with characterization tests** and watch them pass.
3. **Refactor so the concept from step 1 is first-class**, with the feature still
   absent. Characterization tests stay green throughout.
4. **Add the feature** on the restructured base, with its own tests (happy path
   plus critical failure paths). If it does not slot in cleanly, the step 3 shape
   was wrong — fix the shape, do not wedge the feature in.

If a request is genuinely trivial (a copy tweak, a typo), skip the ceremony, but
say so and say why it is safe.

## Best Practices

Adhere to best practices and consider your future self. Use encapsulation, dependency inversion, separation of concerns, single responsibility, composition over inheritance, pure functions, and clear boundaries between modules often and when they fit the situation. Never shoehorn a pattern in just to use the pattern. Never skip on a pattern refactor just because there's a quick, low-blast-radius change that makes it work.

Our primary goal is to have code that is easy to reason about and make changes to in the future.
