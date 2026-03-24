#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for project in "$SCRIPT_DIR"/*/; do
    project_name=$(basename "$project")
    echo "=== Processing $project_name ==="

    ak_dir="$project/ak"
    bl_dir="$project/bl"

    if [ -d "$ak_dir/external-src" ]; then
        echo "Removing $ak_dir/external-src"
        rm -rf "$ak_dir/external-src"
    fi

    if [ -d "$ak_dir/src" ]; then
        echo "Removing $ak_dir/src"
        rm -rf "$ak_dir/src"
    fi

    if [ -d "$bl_dir/external-src" ]; then
        echo "Removing $bl_dir/external-src"
        rm -rf "$bl_dir/external-src"
    fi

    if [ -d "$bl_dir/src" ]; then
        echo "Removing $bl_dir/src"
        rm -rf "$bl_dir/src"
    fi

    echo "Running ak build -j 16 in $ak_dir"
    if (cd "$ak_dir" && ak build -j 16 && ak sparse) >/dev/null 2>&1; then
        echo "ak build: OK"
    else
        echo "ak build: FAILED"
        exit 1
    fi

    echo "Running bl build in $bl_dir"
    if (cd "$bl_dir" && bl build) >/dev/null 2>&1; then
        echo "bl build: OK"
    else
        echo "bl build: FAILED"
        exit 1
    fi

    echo "Running check_diff.sh between bl/external-src and ak/external-src"
    "$SCRIPT_DIR/../dev_tool/check_diff.sh" "$bl_dir/external-src" "$ak_dir/external-src"

    echo "=== Done with $project_name ==="
    echo ""
done

echo "All projects processed!"
