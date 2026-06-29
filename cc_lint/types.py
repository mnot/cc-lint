from typing import Dict, List, NotRequired, TypedDict


class SampleType(TypedDict):
    url: str
    vars: Dict[str, str]
    site: NotRequired[str]


class NoteDataType(TypedDict):
    count: int
    samples: List[SampleType]
    vars: Dict[str, Dict[str, int]]
    var_samples: NotRequired[Dict[str, Dict[str, List[SampleType]]]]
    sites_hll: NotRequired[List[int]]
    numeric_maxes: NotRequired[Dict[str, Dict[str, int]]]
    # Per-infrastructure-layer occurrence counts: how many times this note
    # fired on responses matching each fingerprint layer (issue #4). Layers
    # overlap, so these need not sum to ``count``.
    by_layer: NotRequired[Dict[str, int]]
