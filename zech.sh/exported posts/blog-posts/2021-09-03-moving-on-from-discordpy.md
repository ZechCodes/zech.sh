---
title: "Moving On From Discord.py"
slug: "moving-on-from-discordpy"
published: "2021-09-03T00:19:45.576Z"
tags: ["Python", "Python 3", "python beginner", "python projects"]
---

I run a ~4k member [Discord server](https://discord.gg/sfHykntuGy) that helps people who want to learn to code. Being on Discord we see a lot of people who are asking how to make their own Discord bots. Recently the maintainer for the most popular Python Discord API client has stepped away and archived the project. This has understandably led a lot of people to ask us what API clients we think are best. So I'm gonna give a quick look at a couple of the options I've seen pop up.

## What Options Are There?

Thankfully in the wake of [Discord.py](https://discordpy.readthedocs.io/en/stable/) going away a bunch of people have stepped forward to maintain their own forks of the project. So I'm gonna quickly cover two of the better ones I've found:

- [nextcord](https://nextcord.readthedocs.io/en/latest/index.html)
- [pycord](https://pycord.readthedocs.io/en/latest/index.html)

### nextcord

[nextcord](https://nextcord.readthedocs.io/en/latest/index.html) is a recent fork of [Discord.py](https://discordpy.readthedocs.io/en/stable/). It merged some of the alpha changes that were in the project to add support for buttons, drop downs, and threads. As of writing this they had not yet implemented slash commands, however everything is there, just need to add the necessary logic to tie it all together.

This project also renames the package, so when importing it'll be `nextcord` not `discord`.

#### Features

- Commands (using the same implementation as [Discord.py](https://discordpy.readthedocs.io/en/stable/))
- Extensions (the same cog implementation as [Discord.py](https://discordpy.readthedocs.io/en/stable/))
- Threads
- Buttons/drop downs
- Near perfect compatibility with bots written for [Discord.py](https://discordpy.readthedocs.io/en/stable/)

#### Issues

- Since the package is renamed it is necessary to update all of your code with the new import
- No slash commands (yet)
- Datetimes have been made timezone aware, this breaks some code using datetimes on any Discord object.

### pycord

[pycord](https://pycord.readthedocs.io/en/latest/index.html) is another recent fork of [Discord.py](https://discordpy.readthedocs.io/en/stable/). It also merged the alpha changes from [Discord.py](https://discordpy.readthedocs.io/en/stable/) adding support for buttons, drop downs, and threads. Additionally they have begun implementing their own bot class that adds support for slash commands.

This project kept the `discord` package name, so migrating is simpler but it is necessary to uninstall [Discord.py](https://discordpy.readthedocs.io/en/stable/) to avoid conflicts. This has the added benefit of maintaining support for any packages built for [Discord.py](https://discordpy.readthedocs.io/en/stable/).

#### Features

- Commands (using the same implementation as [Discord.py](https://discordpy.readthedocs.io/en/stable/))
- Extensions (the same cog implementation as [Discord.py](https://discordpy.readthedocs.io/en/stable/))
- Threads
- Buttons/drop downs
- Near perfect compatibility with bots written for [Discord.py](https://discordpy.readthedocs.io/en/stable/)
- Mostly compatible with existing packages designed to be used with [Discord.py](https://discordpy.readthedocs.io/en/stable/)

#### Issues

- Since the package shares the `discord` name with the [Discord.py](https://discordpy.readthedocs.io/en/stable/) package it is necessary to uninstall [Discord.py](https://discordpy.readthedocs.io/en/stable/) before installing pycord, this will ensure there are no conflicts.
- Datetimes have been made timezone aware, this breaks some code using datetimes on any Discord object.
- It's my opinion that adding yet another bot client class is adding more bloat.

## How To Migrate

Thankfully migrating isn't too difficult with either nextcord or pycord.

### Migrating to nextcord

1. Uninstall [Discord.py](https://discordpy.readthedocs.io/en/stable/) by running `pip uninstall discord.py`
2. Install nextcord by running `pip install nextcord`
3. Search for `discord` in all of your bot's files and replace it with `nextcord`
   *Watch out for webhooks, you'll need to make sure they don't get changed to `nextcord.com`*
4. Anywhere that you're using `thing_url` you'll need to change to `thing.url`. Do a search and replace for all of these converting the `_` to a `.`
   1. `avatar_url` -> `avatar.url`
   2. `banner_url` -> `banner.url`
   3. `default_avatar_url` -> `default_avatar.url`
   4. `icon_url` -> `icon.url`
5. If you're using the [Discord.py](https://discordpy.readthedocs.io/en/stable/) webhook implementation you'll need to change `adapter=AsyncWebhookAdapter(session)` to `adpater=session`.

### Migrating to pycord

1. Uninstall [Discord.py](https://discordpy.readthedocs.io/en/stable/) by running `pip uninstall discord.py`
2. Install pycord by running `pip install pycord`

### Migrating Datetimes

Python has two kinds of datetimes: naive and timezone aware. Naive datetimes have no timezone set while timezone aware datetimes have a timezone. Because Python doesn't know the timezone for a naive datetime you cannot do math or compare them with a timezone aware datetime.

Both nextcord and pycord inherited a breaking change from [Discord.py](https://discordpy.readthedocs.io/en/stable/) that changed all datetimes on Discord objects from naive to aware. So to fix that, anywhere that you have a time comparison or are doing math with one, you need to make your datetime timezone aware.

The good news is, it's fairly simple to do, as Discord objects are all using UTC. Do this to all of your datetimes:

```python
from datetime import timezone

...
your_datetime.astimezone(timezone.utc)
```

## Thanks For Reading

I hope you found this helpful! If you have any questions or need any further help feel free to hit me up on [Discord (where I'm Zech)](https://discord.gg/sfHykntuGy) or [Twitter](https://twitter.com/ZechCodes).
