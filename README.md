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

#### How it looks

## Benchmarks

### Ak benchmarks

### Bl benchmarks
