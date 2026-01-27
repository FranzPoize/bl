hyperfine \
  --setup "./clean.sh" \
  --warmup 1 \
  -m 5 \
  'python -m bl -c noukies-spec.yaml -z noukies-frz.yaml' \
  'python -m bl -c abilis-spec.yaml' \
  'python -m bl -c prodotti-spec.yaml'
