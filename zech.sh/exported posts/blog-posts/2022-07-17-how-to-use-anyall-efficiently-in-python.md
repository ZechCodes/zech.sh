---
title: "How To Use Any/All Efficiently in Python"
slug: "how-to-use-anyall-efficiently-in-python"
published: "2022-07-17T14:36:36.032Z"
tags: ["Python", "coding", "efficiency", "Python 3", "Programming Tips"]
---

One day you're tasked with checking if the number 200 million is in the range of 0 to 1 billion. Super trivial I know, just use the `any` function with a listcomp, bam, done.

```python
def find_200_million() -> bool:
    return any([number == 200_000_000 for number in range(1_000_000_000)])
```

Not so fast though! You've got 1 billion numbers and it just hangs when you run it... For me, it hangs for about 42 seconds.

## Why? How can we prevent it from hanging?

First off let's understand what is happening and then we'll see if we can't find a way to prevent it from hanging.

When we run that function two things happen:

1. The listcomp runs, building a list of ~1 billion bools checking if each number equals 200 million.
2. `any` then runs, iterating through the list checking if any of the items are `True`, as soon as a `True` is found, it returns.

So there are two loops iterating 1.2 billion times! `any` will iterate to the 200 millionth value fairly quickly. The listcomp, on the other hand, is building a massive list of 1 billion boolean values. That takes a lot of time.

When I ran it, the listcomp took about 40 seconds to complete while `any` found the first `True` in under 2 seconds. Now that we know what's happening, how can we improve this performance? The primary issue is that there are a ton of values and it takes time to create the list. So if we can reduce the number of values that get added to the list it should be able to finish more quickly.

## Implementing the listcomp more efficiently!

The list can be made smaller by using the listcomp's `if` filtering syntax. This will cause the generated list to only contain `True` values and no `False` values.

```python
def find_200_million() -> bool:
    return any([True for number in range(1_000_000_000) if number == 200_000_000])
```

Now we're generating a list that only contains one item for each number that was equal to 200 million. So we get a list with only a single item.

Testing this I find the listcomp finishes in 33 seconds, about 7 seconds faster, and `any` finishes instantly since it only needs to search a list with a single item. So it's gone from taking about 42 seconds to only taking 33!

## Can this be any faster?

33 seconds is still a lot of time, how can we further improve this? Currently, the listcomp is iterating through all 1 billion items, checking each one, and then giving back a list. What if we used a generator expression instead? Would that help?

```py
def find_200_million() -> bool:
    return any(True for number in range(1_000_000_000) if number == 200_000_000)
```

Running this it finishes in just 6 seconds!

## Why is the generator expression so fast?

Generator expressions aren't faster than listcomps. Their only advantage is that they create the next item on request, whereas the listcomp generates the entire list ahead of time. This is great when using `any` since it will stop as soon as it finds a match.

Let's break down the steps this code is going through to better understand how it works.

1. A generator is created that will go from 0 to 999,999,999 and will yield `True` for each value that is equal to 200 million.
2. `any` is passed the generator and requests the first value from it.
3. The generator begins iterating through the numbers until it finds one that meets the condition of equalling 200 million. It then yields a `True` back to `any`.
4. `any` gets the yielded `True` and returns, checking no more values.

So, this is faster because nothing is iterating past the 200 millionth number in the range! If on the other hand, we had been searching for the last number in the range (999,999,999), this would have been about as fast as using a listcomp. It's only faster when the first value that meets the condition is not at the end of the search space.

## Ok, but what if we didn't use the filtering if?

So, this raises the question, what if we had the condition as the yielded value and didn't use the `if` filtering syntax on the generator expression?

```py
def find_200_million() -> bool:
    return any(number == 200_000_000 for number in range(1_000_000_000))
```

This finishes in about 9 seconds, so it's about 3 seconds slower than using the filtering `if`. This is because now the generator is yielding `False` for the numbers 0 to 199,999,999, and `any` is having to do 200 million checks before it's done. That's 199,999,999 more checks than when we used the filtering `if`.

## In summary

Using the filtering `if` syntax will narrow the search space that `any` has to scan and when done correctly `any` will only have to check the first value it sees.

Also, use generator expressions. They're not always faster but they will stop as soon as `any` is done, often saving time and always saving memory!

## Oh, wait, what about the all function?

Does any of this apply to the `all` function? Can we make it faster too? Yes! We can!

`any` is looking for `True` values and will return `True` when it finds the first one, or `False` if it finds none. `all` does the exact opposite, it looks for `False` and returns `False` when it finds the first one, or `True` if it finds no `False` values.

So you can apply the same optimization techniques, just switch your filtering `if` to look for the failure condition and yield `False` not `True`!

```py
def all_not_equal_to_200_million() -> bool:
    return all(False for number in range(1_000_000_000) if number == 200_000_000)
```

In this example, it yields `False` only if it finds a number that meets the failure condition of equaling 200 million. If no numbers equal 200 million, nothing will ever be yielded, and `all` will return `True` since the generator is empty.
