---
title: "Terrible But Cool Code - Metaclasses"
slug: "terrible-but-cool-code-metaclasses"
published: "2021-08-19T16:55:59.689Z"
tags: ["Python", "Python 3", "python beginner", "classes", "oop"]
---

So the other day I was picking a challenge on Edabit for my Discord server. I came across [Counting Instances Created from a Class](https://edabit.com/challenge/TkbgxTEn7rxd9hmx7), which gave me an odd idea. The challenge asks you to create a class that counts the number of instances that are created.

Simple enough, clearly an obvious case for using Python's metaclasses!!!

A metaclass allows us to modify the behavior of a class object, essentially treating the class as an instance of the metaclass. As an example:

```python
class MyMetaclass(type):
    ...

class Example(metaclass=MyMetaclass):
    ...

print(type(Example))
```

That will print `<class '__main__.MyMetaclass'>` telling us that the `Example` class is a type of `MyMetaclass`.

So if we want to change functionality like add an instance counter we could add a dunder init and dunder call like so:

```python
class CounterMetaclass(type):
    def __init__(cls, name, bases, attrs):
        super().__init__(name, bases, attrs)
        cls.count = 0

    def __call__(cls, *args, **kwargs):
        cls.count += 1
        return super().__call__(*args, **kwargs)


class Example(metaclass=CounterMetaclass):
    ...


inst_1 = Example()
inst_2 = Example()
print(Example.count)
```

Which will print `2` since 2 instances were created.

That works because when a class is first created (`Example` variable is assigned) the metaclass's dunder init is called passing in the name of the class ("Example"), what classes it inherits from, and all the attributes and methods that the class has as a dictionary of name/value pairs.

The dunder call is run anytime that the class (in this case `Example`) is called to create an instance (for example `inst_1 = Example()`).

## Don't Do It That Way

This is actually a terrible approach because it hides implementation details in a metaclass. This could lead to code behaving in an unexpected way that is hard to debug. It also adds a lot of complexity requiring you to create a second class (the metaclass) and two custom dunder methods. Metaclasses should generally be avoided.

## Do This Instead

Just do it this way. Much clearer and all implementation details are in the class itself making it easier to debug and understand.

```python
class Example:
    count = 0

    def __init__(self):
        Example.count += 1


inst_1 = Example()
inst_2 = Example()
print(Example.count)
```

## So Why Show Us This?

So if metaclasses should be avoided, why'd I write this blog post? Mostly because I think it's valuable to understand how things work under the hood. Knowing the steps that Python is taking to create a class helps with understanding how each class functions and why certain things behave how they do.

Hope you found this interesting and that you learned something new!
