#!/usr/bin/env bash
# 0.2 对译硬保证：Python core 与 Rust duo-core 同输入必须逐字节同输出。
# 用法: src/rustduo/scripts/parity_check.sh  （在仓库根目录或任意位置执行均可）
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

cd "$repo/src/rustduo"
cargo build --example parity_dump --quiet
./target/debug/examples/parity_dump > /tmp/duo-parity-rust.tsv

cd "$repo"
uv run python src/rustduo/parity/python_dump.py > /tmp/duo-parity-python.tsv

if diff -q /tmp/duo-parity-python.tsv /tmp/duo-parity-rust.tsv > /dev/null; then
        echo "PARITY_IDENTICAL: $(wc -l < /tmp/duo-parity-python.tsv) lines byte-for-byte"
else
        echo "PARITY FAILED:" >&2
        diff /tmp/duo-parity-python.tsv /tmp/duo-parity-rust.tsv | head -40 >&2
        exit 1
fi
