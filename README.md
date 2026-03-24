# bl
[![CI](https://github.com/FranzPoize/bl/actions/workflows/ci-publish.yml/badge.svg)](https://github.com/FranzPoize/bl/actions/workflows/ci-publish.yml) [![codecov](https://codecov.io/github/FranzPoize/bl/graph/badge.svg?token=JWD4KU0PEN)](https://codecov.io/github/FranzPoize/bl)

## Why BL

Because `ak` is a bit slow and I was tired of waiting around

## Install

`pipx install bl-odoo`

## Usage

For all those command `bl` will try to look in the current directory. If it does find the spec file
 it will try to look in the child `odoo` directory. (i.e. you can launch `bl` in the root of you project)
You can also override the default paths and verbosity with:

- `-c/--config`: path to the project spec file (default: `spec.yaml`)
- `-z/--frozen`: path to the frozen spec file (default: `frozen.yaml`)
- `-o/--config-override`: path to an override config to extend the project specification
- `-j/--concurrency`: number of concurrent tasks (default: `28`)
- `-b/--use-bindfs`: use bindfs instead of creating symlinks (requires `user_allow_other` in `/etc/fuse.conf`)
- `-w/--workdir`: working directory, defaults to the directory of `--config`
- `--log-level`: one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (default: `WARNING`)

### Build

```bash
bl build [-c PATH_TO_SPEC] [-z PATH_TO_FROZEN] [-o CONFIG_OVERRIDE] [-j CONCURRENCY] [-b/--use-bindfs] [-w WORKDIR] [--log-level LEVEL]
```

#### What does it do
It does what ak build does

#### Params
* `PATH_TO_SPEC` path to your spec (default: `spec.yaml`)
* `PATH_TO_FROZEN` path to your frozen spec (default: `frozen.yaml`)
* `CONFIG_OVERRIDE` path to an override config to extend the project specification
* `CONCURRENCY` number of module clone simultaneously (default: `28`)
* `--use-bindfs` use bindfs instead of creating symlinks (requires `user_allow_other` in `/etc/fuse.conf`)
* `WORKDIR` working directory; if omitted, the directory containing `spec.yaml`
* `LEVEL` log level (see `--log-level` above)

#### How it looks
<img width="1683" height="756" alt="bl_build" src="https://github.com/user-attachments/assets/22fc1565-3a54-4f57-9b85-a11263b9b536" />

### Freeze

```bash
bl freeze [-c PATH_TO_SPEC] [-z PATH_TO_FROZEN] [-o CONFIG_OVERRIDE] [-j CONCURRENCY] [-w WORKDIR] [--log-level LEVEL]
```

#### What does it do
It does what ak freeze does

#### Params
* `PATH_TO_SPEC` path to your spec (default: `spec.yaml`)
* `PATH_TO_FROZEN` path to your frozen spec (default: `frozen.yaml`)
* `CONFIG_OVERRIDE` path to an override config to extend the project specification
* `CONCURRENCY` number of module clone simultaneously (default: `28`)
* `WORKDIR` working directory; if omitted, the directory containing `spec.yaml`
* `LEVEL` log level (see `--log-level` above)

### Diff

```bash
bl diff [-c PATH_TO_SPEC] [-z PATH_TO_FROZEN] [-o CONFIG_OVERRIDE] [-j CONCURRENCY] [-w WORKDIR] [--log-level LEVEL]
```

#### What does it do
Shows diff for all dirty repos in the project.

#### Params
* `PATH_TO_SPEC` path to your spec (default: `spec.yaml`)
* `PATH_TO_FROZEN` path to your frozen spec (default: `frozen.yaml`)
* `CONFIG_OVERRIDE` path to an override config to extend the project specification
* `CONCURRENCY` number of module clone simultaneously (default: `28`)
* `WORKDIR` working directory; if omitted, the directory containing `spec.yaml`
* `LEVEL` log level (see `--log-level` above)

### Clean

```bash
bl clean [-c PATH_TO_SPEC] [-o CONFIG_OVERRIDE] [-w WORKDIR] [--log-level LEVEL] [--remove] [--unlink] [--force] [--dry-run]
```

#### What does it do
By default it scans all repos in the spec for dirty git state and resets them with `git reset --hard`.
With `--remove` deletes `src` and `external-src` directories instead of resetting diry repo.
With `--unlink` it also cleans the links directory.
With `--dry-run` it just outputs what it would do without doing it.
With `--force` it removes confirmation prompts.

#### Params
* `PATH_TO_SPEC` path to your spec (default: `spec.yaml`)
* `CONFIG_OVERRIDE` path to an override config to extend the project specification
* `WORKDIR` working directory; if omitted, the directory containing `spec.yaml`
* `LEVEL` log level (see `--log-level` above)
* `--remove` also delete `src` and `external-src` directories
* `--unlink` clean the links directory
* `--force` remove confirmation prompts
* `--dry-run` just output dirty repos without resetting

## Odoo is taking a really long time to clone

Yes !

You can add a locales entry to your odoo repo in `spec.yaml` like so:
```yaml
odoo:
  modules:
    - account
    ...
  remotes:
    odoo: https://github.com/odoo/odoo
  merges:
    - odoo 14.0
  locales:
    - fr
    - en
```
It will only download the french and english translation instead of all of them
- without locales: 849MB and 40 seconds fresh build
- with locales fr, en: 169MB and 27 seconds fresh build

⚠️ WARNING: you must list all the odoo modules you need if you use the locales property

## I have warnings about patch globs

There is a new property to handle `git am <patch_glob>` (the old one still works but I hope to remove it at some point)

Before:
```yaml
folder_name:
  modules:
    ...
  remotes:
    ...
  merges:
    ...
  shell_command_after:
    - git am ../../patches/patch_folder/*
```
After:
```yaml
folder_name:
  modules:
    ...
  remotes:
    ...
  merges:
    ...
  patch_globs:
    - ../../patches/patch_folder/*
```


## Benchmarks

### Ak benchmarks
#### Fresh install
<img width="1462" height="347" alt="ak_bench_cold" src="https://github.com/user-attachments/assets/e29cd3d9-831c-43c1-8f29-e040ebee5740" />

#### Already cloned once
<img width="1419" height="356" alt="ak_bench_hot" src="https://github.com/user-attachments/assets/47b5756e-efe1-4272-82b7-e160f73af1be" />

### Bl benchmarks
#### Fresh install
<img width="1335" height="343" alt="bl_bench_cold" src="https://github.com/user-attachments/assets/a64ba1c4-17bd-4017-acfd-5749df505f50" />

#### Already cloned once
<img width="1373" height="342" alt="bl_bench_hot" src="https://github.com/user-attachments/assets/b11e60c2-368b-496c-bc88-f5a765f44bfe" />

### Results
|Type| AK | BL |
|----|----|----|
|Cold| ~100s | 2 - 10x faster |
|Hot| 3-20s | 2 - 10x faster |

