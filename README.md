# bl

## Why BL

Because `ak` is slow and `bl` is fast
Because `ak` crashes if anything goes wrong
Because `ak` error are impossible to find

## Usage

### Build

```bl build -c <path_to_spec.yaml> -z <path_to_frozen.yaml> -j <concurrency>```

#### Params
* `path_to_spec.yaml` should be the path to your spec (default: spec.yaml)
* `path_to_frozen.yaml` should be the path to your spec (default: frozen.yaml)
* `concurrency` number of module clone simultaneously (default: 28)

#### How it looks

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

### Bl benchmarks
