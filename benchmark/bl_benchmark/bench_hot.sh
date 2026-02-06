hyperfine \
  --setup "./clean.sh" \
  --warmup 1 \
  -m 5 \
  'python -m bl build -c noukies-spec.yaml -z noukies-frz.yaml' \
  'python -m bl build -c abilis-spec.yaml' \
  'python -m bl build -c prodotti-spec.yaml'
