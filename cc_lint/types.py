from typing import TypedDict, Dict, List, Any, NotRequired

class SampleType(TypedDict):
    url: str
    vars: Dict[str, str]

class NoteDataType(TypedDict):
    count: int
    samples: List[SampleType]
    vars: Dict[str, Dict[str, int]]
    var_samples: NotRequired[Dict[str, Dict[str, List[SampleType]]]]

class MRJobAggregateType(TypedDict):
    count: int
    samples: List[SampleType]
    vars: Dict[str, Any]
    var_samples: Dict[str, Any]
    fields: NotRequired[Dict[str, int]]
    unprocessed: NotRequired[Dict[str, int]]
