---
title: "Try, Except … Else?"
slug: "try-except-else"
published: "2022-03-12T21:41:56.723Z"
tags: ["Python", "Python 3", "python beginner", "coding", "error handling"]
---

If you've spent anytime playing around with Python you have probably learned about the `try` statement and its `except` clause. It's a handy tool for capturing and dealing with exceptions before they break your program.

There's another clause you can use with `try` that I don't think most people know about. For those who are aware of it, I'm not sure that they fully understand why they'd ever want to use it. This other clause is `else`.

## What does it do?

The `else` clause in a `try` statement is pretty simple, its code runs right after the `try` block finishes, but only if no exceptions were raised. Let's look at an example.

```py
try:
    index = my_list.index("foo")
    print(f"We found foo at index {index}")
except ValueError:
    print("Foo was not found in the list")
```

This code is pretty straightforward, look for the string `"foo"` in a list. If it's found, a message with the index of the string is printed, if it's not found a message indicating it wasn't in the list will be printed.

Now let's rewrite it using an `else` clause.

```py
try:
    index = my_list.index("foo")
except ValueError:
    print("Foo was not found in the list")
else:
    print(f"We found foo at index {index}")
```

This code has the exact same output. It runs the line in the `try` block, stores the index of the string in a variable, and when there are no exceptions it moves down to the `else` and prints out the message. The `else` has access to any variables created in the `try` block, so we can safely use the `index` variable.

Pretty simple, but…

## Why would you use it?

Alright, so if the output of both snippets is the same, why even use the `else` clause? Ultimately it comes down to the idea that you should put as few lines in the `try` block as you possibly can.

You want to limit the lines of code in the `try` block to just those that you expect to give an error. If, for example, you have 2 lines in the try block, the first might give you a `ValueError` that you want to handle. The other line you don't expect will ever give you an error.

The concern here becomes "what if someday that second line does given an error?" In that extreme case you'd wanna know about the failure. Having it inside the `try` block *might* hide it from you.

So, simply the idea is to limit the number of lines you've placed inside the `try` block so that if anything decides to fail in an unexpected way you'll find out. That's why the `else` clause is very useful! It lets you write code that you want to observe for exceptions and follow it by code that you don't want to observe.

## Let's look at another example!

Consider this code.

```py
try:
    user_data = user.load_data()
    user_data.coins += 1
    user_data.save()
except FileNotFoundError:
    user.setup_data()
```

It loads a user's data from a file, placing it into some kind of object. It then increments the coin count by 1 and saves the changes. If the user's data file isn't found, it'll raise a `FileNotFoundError`. This in turn will cause `setup_data` to be called and create a new file with a set of default values.

What happens though, if the `save` method needs to access another file and gets its own `FileNotFoundError`. We won't know it happened in the `save` method because we're expecting only the `load_data` method to give that exception. `setup_data` will then run and overwrite the user's data, and we won't actually know why it happened.

This is where the `else` clause comes in to make our lives much easier.

```py
try:
    user_data = user.load_data()
except FileNotFoundError:
    user.setup_data()
else:
    user_data.coins += 1
    user_data.save()
```

Now if the `save` method fails we'll see the exception in our console/logs and we'll know right away that we need to fix the `save` method and that nothing went wrong with the `load_data` method.

## In conclusion, use it!

The `try` statement's `else` clause is a powerful tool at our disposal. It helps us write code that's much easier to understand and maintain. It allows us to write code with a clear flow, but without costing us valuable runtime context.

Have a great day and be sure to use the `else` clause in your next project!
