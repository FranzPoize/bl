hyperfine \
  --prepare 'rm -rf external-src/; rm -rf src' \
  -m 2 \
  'python -m bl build -c noukies-spec.yaml -z noukies-frz.yaml' \
  'python -m bl build -c abilis-spec.yaml' \
  'python -m bl build -c prodotti-spec.yaml'
