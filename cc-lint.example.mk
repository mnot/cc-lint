# Example local configuration for cc-lint.
#
# Copy this file to cc-lint.mk, replace YOUR-BUCKET with your S3 bucket,
# and uncomment / edit any defaults you want to override. Anything you
# don't set here inherits from cc-lint.defaults.mk via the top-level
# Makefile.

# Required: where EMR results land, where paths chunks live, and where
# the pre-built dependency wheel bundle lives. All three should be
# under your control with appropriate access policies (the wheel bucket
# in particular needs operator-write / EMR-read access -- whatever can
# write there runs as root on every EMR node).
OUTPUT_DIR    = s3://YOUR-BUCKET/cc-lint/results/
PATHS_PREFIX  = s3://YOUR-BUCKET/cc-lint/paths/
WHEEL_S3_PATH = s3://YOUR-BUCKET/cc-lint/wheels/

# Common overrides. Uncomment and edit to deviate from defaults.
# CRAWL_ID       = CC-MAIN-2026-12
# LOCAL_CRAWL_ID = CC-MAIN-2024-18
# TOP_N          = 50000
# LOCAL_TOP_N    = 1000
# SAMPLE_TOP_N   = 10000
# MAP_TASKS      = 1600
# REDUCES        = 20
# MRJOB_CLEANUP  = TMP
