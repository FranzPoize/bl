hyperfine --prepare 'rm -rf external-src/; rm -rf src' --parameter-scan num_conc 4 16 'python -m bl noukies-spec.yaml {num_conc}'
