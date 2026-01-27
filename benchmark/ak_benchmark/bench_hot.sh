hyperfine \
  --setup "./clean.sh" \
  --warmup 1 \
  -m 2 \
  'ak build -j 16 -c noukies-spec.yaml' \
  'ak build -j 16 -c abilis-spec.yaml' \
  'ak build -j 16 -c prodotti-spec.yaml'
