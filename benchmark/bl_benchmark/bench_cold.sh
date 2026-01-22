hyperfine --prepare 'rm -rf external-src/; rm -rf src' -m 5 --parameter-scan num_conc 4 8 'python -m bl noukies-spec.yaml $((4*{num_conc}))'
