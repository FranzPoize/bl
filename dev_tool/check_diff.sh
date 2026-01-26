#!/usr/bin/env bash

set -e

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <path1> <path2>"
    exit 1
fi

PATH1="$1"
PATH2="$2"

# Function to compute a single MD5 for all .py files in a directory
dir_md5() {
    local dir="$1"
    find "$dir" -type f -name "*.py" \
        -exec md5sum {} + \
        | awk '{print $1}' \
        | sort \
        | md5sum \
        | awk '{print $1}'
}

# Get common directories (by name)
dirs1=$(find "$PATH1" -mindepth 1 -maxdepth 1 -type d -printf "%f\n" | sort)
dirs2=$(find "$PATH2" -mindepth 1 -maxdepth 1 -type d -printf "%f\n" | sort)

common_dirs=$(comm -12 <(echo "$dirs1") <(echo "$dirs2"))

for dir in $common_dirs; do
    md51=$(dir_md5 "$PATH1/$dir")
    md52=$(dir_md5 "$PATH2/$dir")

    if [ "$md51" != "$md52" ]; then
        echo "Directory differs: $dir"

        # Get relative file lists
        files1=$(cd "$PATH1/$dir" && find . -type f -name "*.py" | sort)
        files2=$(cd "$PATH2/$dir" && find . -type f -name "*.py" | sort)

        all_files=$(comm -12 <(echo "$files1") <(echo "$files2"))

        for file in $all_files; do
            f1="$PATH1/$dir/$file"
            f2="$PATH2/$dir/$file"

            md51=$(md5sum "$f1" | awk '{print $1}')
            md52=$(md5sum "$f2" | awk '{print $1}')

            if [ "$md51" != "$md52" ]; then
                echo "  DIFFER: $file"
                echo "    $PATH1 ($md51)"
                echo "    $PATH2 ($md52)"
            fi
        done

        # Files only in PATH1
        only1=$(comm -23 <(echo "$files1") <(echo "$files2"))
        for file in $only1; do
            echo "  ONLY IN $PATH1: $file"
        done

        # Files only in PATH2
        only2=$(comm -13 <(echo "$files1") <(echo "$files2"))
        for file in $only2; do
            echo "  ONLY IN $PATH2: $file"
        done

        echo "----------------------------------------"
    fi
done

