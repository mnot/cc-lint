"""Common Crawl path helpers shared between the local CLI and EMR.

The local CLI (cc_lint.crawling) and the EMR worker
(cc_lint.emr.warc_source) both translate a Common Crawl WARC path to
its WAT sibling before fetching, so the rewrite logic lives in one
place rather than being duplicated.
"""


def warc_path_to_wat(path: str) -> str:
    """Rewrite a Common Crawl WARC path to its sibling WAT path.

    Idempotent: passing a path that's already a WAT path returns it
    unchanged.

    >>> warc_path_to_wat("crawl-data/.../warc/X.warc.gz")
    'crawl-data/.../wat/X.warc.wat.gz'
    """
    if "/warc/" in path:
        path = path.replace("/warc/", "/wat/")
    if path.endswith(".warc.gz"):
        path = path.replace(".warc.gz", ".warc.wat.gz")
    return path
