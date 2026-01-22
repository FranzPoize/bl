# Unrelated merge

Trying to merge with --allow-unrelated-histories does not seems to speed up the CLI. This is because most branch without common history usually don't merge cleanly
and if they do there is a big deal of chance that merging without --allow-unrelated-histories will do the same job.
