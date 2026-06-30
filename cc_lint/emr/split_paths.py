#!/usr/bin/env python3
"""Split a WARC paths file into N chunks and upload to an S3 prefix.

Usage: python -m cc_lint.emr.split_paths <input> <s3-output-prefix> <n-tasks> [max-paths]
  input: local .txt/.gz file or s3:// path (requester-pays handled automatically)
  max-paths: optional global cap on the number of paths to upload
"""

import argparse
import gzip
import math
import os
import subprocess
import tempfile
from typing import List

import boto3
from botocore.exceptions import ClientError


def read_paths(source: str) -> List[str]:
    if source.startswith("s3://"):
        parts = source[5:].split("/", 1)
        bucket, key = parts[0], parts[1]
        s3 = boto3.client("s3", region_name="us-east-1")
        try:
            resp = s3.get_object(Bucket=bucket, Key=key, RequestPayer="requester")
        except ClientError:
            resp = s3.get_object(Bucket=bucket, Key=key)
        data = resp["Body"].read()
        if key.endswith(".gz"):
            data = gzip.decompress(data)
        return [line for line in data.decode("utf-8").splitlines() if line.strip()]

    opener = gzip.open if source.endswith(".gz") else open
    with opener(source, "rt", encoding="utf-8") as path_file:
        return [line.rstrip("\n") for line in path_file if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split a WARC paths file into chunks and upload them to S3."
    )
    parser.add_argument("source", help="Local .txt/.gz file or s3:// WARC paths file")
    parser.add_argument("dest", help="S3 prefix where chunk files should be uploaded")
    parser.add_argument("n_tasks", type=int, help="Number of path chunks to create")
    parser.add_argument(
        "max_paths",
        nargs="?",
        type=int,
        default=0,
        help="Optional cap on the total number of paths to upload",
    )
    args = parser.parse_args()

    source = args.source
    dest = args.dest.rstrip("/") + "/"
    n_tasks = args.n_tasks
    max_paths = args.max_paths

    print(f"Reading paths from {source}...")
    lines = read_paths(source)
    if max_paths > 0:
        lines = lines[:max_paths]
        print(f"Limited to first {len(lines)} paths.")
    chunk_size = math.ceil(len(lines) / n_tasks)
    print(f"Splitting {len(lines)} paths into {n_tasks} chunks of ~{chunk_size}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        count = 0
        for i in range(n_tasks):
            chunk = lines[i * chunk_size : (i + 1) * chunk_size]
            if not chunk:
                continue
            with open(
                os.path.join(tmpdir, f"chunk-{i:05d}.txt"), "w", encoding="utf-8"
            ) as chunk_file:
                chunk_file.write("\n".join(chunk) + "\n")
            count += 1

        print(f"Uploading {count} chunks to {dest}...")
        subprocess.run(["aws", "s3", "sync", tmpdir + "/", dest], check=True)

    print(f"Done: {count} chunks at {dest}")


if __name__ == "__main__":
    main()
