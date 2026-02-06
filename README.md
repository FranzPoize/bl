# bl

## Why BL

Because `ak` is slow and `bl` is fast

Because `ak` crashes if anything goes wrong

Because `ak` error are impossible to find

## Install

`pipx install bl-odoo`

## Usage

### Build

```bl build -c <path_to_spec.yaml> -z <path_to_frozen.yaml> -j <concurrency>```

#### Params
* `path_to_spec.yaml` should be the path to your spec (default: spec.yaml)
* `path_to_frozen.yaml` should be the path to your spec (default: frozen.yaml)
* `concurrency` number of module clone simultaneously (default: 28)

#### How it looks
<img width="1683" height="756" alt="bl_build" src="https://github.com/user-attachments/assets/22fc1565-3a54-4f57-9b85-a11263b9b536" />

### Freeze

```bl freeze -c <path_to_spec.yaml> -z <path_to_frozen.yaml> -j <concurrency>```

#### Params
* `path_to_spec.yaml` should be the path to your spec (default: spec.yaml)
* `path_to_frozen.yaml` should be the path to your spec (default: frozen.yaml)
* `concurrency` number of module clone simultaneously (default: 28)

## Odoo is taking a really long time to clone

Yes !

You can add a locales entry to your odoo repo in `spec.yaml` like so:
```
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
|Cold| ~100s | 2 -> 10x faster |
|Hot| 3->20s | 2 -> 10x faster |

