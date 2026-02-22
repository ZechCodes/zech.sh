---
title: "Automate Python Package Publishing to PyPI with uv, Trusted Publisher, and GitHub Actions"
slug: "automate-uv-with-trusted-publisher"
published: "2025-03-29T22:10:30.858Z"
tags: ["trusted publisher", "package publishing", "secure publishing", "python package management", "automating python package releases", "Python", "pypi", "UV", "GitHub", "github-actions", "automation", "ci-cd", "Continuous Integration", "continuous deployment", "OpenID Connect"]
---

You made a simple fix to your Python package, but now you've got to publish it again: find the correct commands and figure out authentication with the Python Package Index (PyPI). I've got a solution for you: automate Python package publishing using uv, PyPI's Trusted Publisher, and GitHub Actions!

Today, I'm going to show you how to set up a GitHub Action with uv to securely automate publishing a Python package using PyPI's Trusted Publisher. Using this you're going to be able to `pip install your_awesome_project` from anywhere!

## Tools Used

1. uv is a fast package manager for Python written in Rust. It simplifies setting up your Python project, installing dependencies, and even installing Python!
2. GitHub Actions are free cloud automations for your GitHub repos. You can run code in response to events on your repository!
3. PyPI's Trusted Publisher uses OpenID Connect (OIDC) to securely connect your GitHub Actions to PyPI so you can publish new package versions without passwords or tokens! You can learn more in [this 2023 post on the Python blog](https://blog.pypi.org/posts/2023-04-20-introducing-trusted-publishers/).

## Final Product

You're busy, so let's skip to the end and then we'll work backward:

```yaml
name: Release to PyPI using Trusted Publisher

on:
  release:
    types: [created]

jobs:
  publish:
    name: Publish to PyPI
    runs-on: ubuntu-latest
    environment:
      name: release
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          version: "0.6.10"
      - run: uv build
      - run: uv publish --trusted-publishing always
```

## Usage

1. Put that YAML in `.github/workflows/publish.yaml`
   1. The `.github` folder should be at the root of your repo
   2. You may need to create the `.github` and `workflows` folders
2. Commit and then push the new `publish.yaml` to your repo on GitHub
3. On your repo's GitHub page, create a `release` environment: [read how on GitHub](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment#creating-an-environment)

Next, we need to configure Trusted Publisher on PyPI.

### Creating Publishers for New Packages

![The form for adding a new pending publisher](https://cdn.hashnode.com/res/hashnode/image/upload/v1743283153488/6e4560e1-c0f5-49d2-ad8e-521735738170.png align="center")

If this is a new package that has never been published before, you must register a "pending new publisher." You have to choose a name that is unique on PyPI for your project, however this step **does not** reserve that name for you, you must publish the package to own the name.

At the bottom of [this admin page](https://pypi.org/manage/account/publishing/) begin filling in the form by entering the name you have chosen for your package, it must not belong to another existing package on PyPI. Next, jump to step 3 of the next section.

> **Reminder:** This step does not reserve the package name for you, you must publish the package to own the name.

### Creating Publishers for Existing Packages

![The form for adding a new publisher](https://cdn.hashnode.com/res/hashnode/image/upload/v1743283010760/b3bb737f-2963-468f-8669-e3d43b9951a0.png align="left")

To set up Trusted Publisher for a package that already exists on PyPI, do this:

1. Find your Project in the PyPI admin, [this admin page has all your projects](https://pypi.org/manage/projects/)
2. Click on "Manage" &gt; "Publishing" (left panel)
3. Now add a new publisher
   1. Use your GitHub username (mine would be `ZechCodes`)
   2. The name of your GitHub repository (`Schism` is an example for one of mine)
   3. The name of your workflow file (we named it `publish.yaml`)
   4. The environment name (we created `release`)
   5. Finally, click "Add"

Now, your Python package publishing is automated! You can publish your package by creating a new release from the main page of your GitHub repo.

![A successful run a our GitHub Action to deploy my Nubby package](https://cdn.hashnode.com/res/hashnode/image/upload/v1743283672966/d0d84f4b-4a9e-412d-91c4-b36c8192610c.png align="center")

## How'd We Get To This GitHub Action

uv's docs are incredible, but they don't show how to publish a package with Trusted Publisher. However, there is a slightly dated [example repo](https://github.com/astral-sh/trusted-publishing-examples/blob/24971e1aacf6277a711ae47c7d8a2dbd91f96aea/.github/workflows/release.yml) on the GitHub for Astral, the creators of uv, that shows how to do it.

Taking what's in that repo, I adapted it by:

* Publishing to PyPI when a new GitHub release is created gives a nice, consistent way to have consolidated version notes on GitHub.
* Removing the smoke tests to keep this post as simple as possible.
* Updating the version of `astral-sh/setup-uv` to the latest v5 (find the latest version [here](https://github.com/astral-sh/setup-uv/releases)).
* Pinned the version of uv that's installed to the latest v0.6.10 (find the latest version [here](https://github.com/astral-sh/uv/releases)).

## Leveling Up Our Comprehension

### Understanding Why We Should Use Trusted Publisher

It is much more secure than using tokens or passwords. Tokens and passwords are long-lived. If someone steals them, they can be used over and over to publish whatever they want to our package on PyPI. Trusted Publisher avoids that by transparently assigning short-lived tokens that are useless very soon after they are created. The best part: you don't have to worry about the security of your credentials, there are none.

### Understanding "--trusted-publisher always"

You may have noticed that when we run `uv publish,` we pass `--trusted-publisher always`. It isn't strictly necessary; if Trusted Publisher is available, uv will use it. The setting is a best practice to ensure uv *only* uses Trusted Publisher to publish by failing when it isn't configured correctly.

### Understanding the "id-token" Permission

```yaml
permissions:
  id-token: write
```

This permission is required to use Trusted Publisher. It allows the GitHub Action to request an OIDC token from the GitHub OIDC provider. This short-lived token enables a securely authenticated connection with PyPI without needing an auth token or password.

### Understanding Why This Doesn't Install Python

If you look closely at the `publish.yaml` here, you'll notice it never once installs Python. It's one of uv's superpowers — it handles all of that for you! If you need Python for any reason, use uv's commands to run your code; it handles the rest.

## Making It Better

The `publish.yaml` here is a barebones action that does nothing more than publish our Python package. It's important to not forget that. So here are a few changes that could be made to improve this publish automation:

### Add Smoke Tests

Smoke tests ensure that the code works *before* publishing it to PyPI. This is important so no one downloads our package and finds it doesn't work.

Adding a step right before the final publish step, that ensures your package can be imported goes a long way, even if it's fairly simple (note: *Be sure to update* `your_package` to match the import name of your package):

```yaml
- name: Smoke Test
  run: uv run --isolated --no-project -p 3.13 --with dist/*.whl -c "import your_package"
```

### Add Caching

You can utilize caching to improve performance, especially in larger workflows. The uv docs cover [caching in good detail](https://docs.astral.sh/uv/guides/integration/github/#caching).

To add some basic caching of dependencies that invalidates when the lock file changes, update the `astral-sh/setup-uv` action's step, like this:

```yaml
- uses: astral-sh/setup-uv@v5
  with:
    version: "0.6.10"
    enable-cache: true
    cache-dependency-glob: "uv.lock"
```

### Make uv Pinning Simpler

Pinning the uv version in the workflow automation can be a bit cumbersome when you consider all of the other versions are pinned in the `pyproject.toml`. uv can be configured to pull its required version from the `pyproject.toml` as well.

In your `pyproject.toml` add this:

```toml
[tool.uv]
required-version = "0.6.10"
```

Next, update the `astral-sh/setup-uv` step like this (note: *Don't forget to replace* `path/to/pyproject.toml` with the actual path to your `pyproject.toml`):

```yaml
- uses: astral-sh/setup-uv@v5
  with:
    pyproject-file: "path/to/pyproject.toml"
```

You can learn more about pinning uv's version [here](https://github.com/astral-sh/setup-uv?tab=readme-ov-file#install-a-required-version).

## Conclusion

GitHub can securely and repeatably automate publishing your Python package, helping you focus on your projects without having to track how you publish everything.

Please get in touch with me on Discord or my social media with any questions!

![](https://cdn.hashnode.com/res/hashnode/image/upload/v1743284636565/3ea1ec42-63f1-49d1-b16e-af34c3973443.png align="left")

\[XKCD 1172: Workflow\]([https://xkcd.com/1172/](https://xkcd.com/1172/))
