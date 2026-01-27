hyperfine \
  --prepare 'rm -rf external-src/; rm -rf src' \
  -m 2 \
  'python -m bl -c noukies-spec.yaml -z noukies-frz.yaml' \
  'python -m bl -c abilis-spec.yaml' \
  'python -m bl -c prodotti-spec.yaml'
