import hashlib
import json


MAX_SUBTASK_ID_LENGTH = 64
_HASH_PREFIX = "subtask_"


def build_subtask_id(task_id: str, execution_id: str, logical_key: str) -> str:
    candidate = (
        f"{task_id}_{execution_id}_{logical_key}"
        if execution_id
        else f"{task_id}_{logical_key}"
    )
    if len(candidate) <= MAX_SUBTASK_ID_LENGTH:
        return candidate

    identity = json.dumps(
        [task_id, execution_id, logical_key],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    digest_length = MAX_SUBTASK_ID_LENGTH - len(_HASH_PREFIX)
    return f"{_HASH_PREFIX}{digest[:digest_length]}"
