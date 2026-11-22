#!/bin/sh
# Generates the Python gRPC stubs from proto/ into gen/ (needs python-grpcio-tools).
set -eu
cd "$(dirname "$0")"
out=${1:-gen}
mkdir -p "$out"
# --experimental_allow_proto3_optional is a no-op on current protoc, needed by old ones
python3 -m grpc_tools.protoc --experimental_allow_proto3_optional -I proto --python_out="$out" --grpc_python_out="$out" \
    $(find proto -name '*.proto' | sort)
echo "stubs in $out"
