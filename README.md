# bl

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
- `-j/--concurrency`: number of concurrent tasks (default: `28`)
- `-w/--workdir`: working directory, defaults to the directory of `--config`
- `--log-level`: one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (default: `WARNING`)

### Build

```bash
bl build [-c PATH_TO_SPEC] [-z PATH_TO_FROZEN] [-j CONCURRENCY] [-w WORKDIR] [--log-level LEVEL]
```

#### What does it do
It does what ak build does

#### Params
* `PATH_TO_SPEC` path to your spec (default: `spec.yaml`)
* `PATH_TO_FROZEN` path to your frozen spec (default: `frozen.yaml`)
* `CONCURRENCY` number of module clone simultaneously (default: `28`)
* `WORKDIR` working directory; if omitted, the directory containing `spec.yaml`
* `LEVEL` log level (see `--log-level` above)

#### How it looks
<img width="1683" height="756" alt="bl_build" src="https://github.com/user-attachments/assets/22fc1565-3a54-4f57-9b85-a11263b9b536" />

### Freeze

```bash
bl freeze [-c PATH_TO_SPEC] [-z PATH_TO_FROZEN] [-j CONCURRENCY] [-w WORKDIR] [--log-level LEVEL]
```

#### What does it do
It does what ak freeze does

#### Params
* `PATH_TO_SPEC` path to your spec (default: `spec.yaml`)
* `PATH_TO_FROZEN` path to your frozen spec (default: `frozen.yaml`)
* `CONCURRENCY` number of module clone simultaneously (default: `28`)
* `WORKDIR` working directory; if omitted, the directory containing `spec.yaml`
* `LEVEL` log level (see `--log-level` above)

### Clean

```bash
bl clean [-c PATH_TO_SPEC] [-w WORKDIR] [--log-level LEVEL] [--i-am-stupid] [--dirty]
```

#### What does it do
By default it asks you if you want to delete `external-src` and `src` under the workdir and then deletes them.
With `--i-am-stupid` it deletes those directories without prompting.
With `--dirty` it additionally scans all repos in the spec for dirty git state and offers to clean them with `git reset --hard`.

#### Params
* `PATH_TO_SPEC` path to your spec (default: `spec.yaml`)
* `WORKDIR` working directory; if omitted, the directory containing `spec.yaml`
* `LEVEL` log level (see `--log-level` above)
* `--i-am-stupid` delete `src` and `external-src` non‑interactively
* `--dirty` also inspect repos and optionally clean dirty ones with `git reset --hard`

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

There is a new property to handle "git am <patch_glob>"

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

