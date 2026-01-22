hyperfine --prepare 'rm -rf external-src/; rm -rf src' -m 2 --parameter-scan num_conc 4 6 'ak build -j $((4*{num_conc})) -c noukies-spec.yaml'
