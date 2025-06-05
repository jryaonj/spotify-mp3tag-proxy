from collections.abc import MutableMapping, Sequence
from copy import deepcopy

def merge_missing_props_by_id(
    Ad: list[dict],
    Bd: list[dict],
    *,
    id_key: str = "id",
) -> None:
    """
    In-place update of Ad:
    - Aligns the top-level list by index (since album track positions are generally fixed)
    - For nested list[dict] elements, converts them into dicts keyed by id and then recursively merges
    - Supplements only when Ad is missing information; never overwrites existing values
    """
    if len(Ad) != len(Bd):
        raise ValueError("Top-level list lengths are inconsistent and cannot be merged by index.")

    for a_dict, b_dict in zip(Ad, Bd):
        if not isinstance(a_dict, MutableMapping) or not isinstance(b_dict, MutableMapping):
            raise TypeError("Top-level elements of both Ad and Bd must be dicts.")
        _merge_dict(a_dict, b_dict, id_key=id_key)


def _merge_dict(da: MutableMapping, db: MutableMapping, *, id_key: str) -> None:
    """Recursively fill missing keys in da from db (only adds missing values)"""
    for k, vb in db.items():
        if k not in da:
            # da is completely missing this key; add a deep copy
            da[k] = deepcopy(vb)
            continue

        va = da[k]

        # Case 1: Both are dicts; recurse.
        if isinstance(va, MutableMapping) and isinstance(vb, MutableMapping):
            _merge_dict(va, vb, id_key=id_key)

        # Case 2: Both are lists of dicts with the unique id key; merge based on that key.
        elif (
            isinstance(va, list) and isinstance(vb, list)
            and all(isinstance(x, MutableMapping) and id_key in x for x in vb)
        ):
            _merge_list_by_id(va, vb, id_key=id_key)

        # Case 3: Other types; only override if va is "missing".
        else:
            if _is_missing(va):
                da[k] = deepcopy(vb)


def _merge_list_by_id(la: list[dict], lb: list[dict], *, id_key: str) -> None:
    """Align la ← lb using id_key; order follows lb"""
    index_a = {item[id_key]: item for item in la if id_key in item}

    for item_b in lb:
        uid = item_b[id_key]
        if uid in index_a:
            # Already exists → recursively supplement
            _merge_dict(index_a[uid], item_b, id_key=id_key)
        else:
            # Ad is completely missing this element → append a deep copy
            la.append(deepcopy(item_b))


def _is_missing(value) -> bool:
    """Determine if value is "empty" """
    return value in (None, "", [], {}) or value == 0