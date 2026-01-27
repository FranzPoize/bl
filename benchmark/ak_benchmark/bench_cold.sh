hyperfine \
  --prepare 'rm -rf external-src/; rm -rf src' \
  -m 2 \
  'ak build -j 16 -c noukies-spec.yaml -f noukies-frz.yaml' \
  'ak build -j 16 -c abilis-spec.yaml' \
  'ak build -j 16 -c prodotti-spec.yaml'
