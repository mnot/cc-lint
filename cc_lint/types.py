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
