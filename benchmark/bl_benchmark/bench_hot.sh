hyperfine --warmup 1 -m 5 --parameter-scan num_conc 4 6 'python -m bl noukies-spec.yaml $((4*{num_conc}))'
