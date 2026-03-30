# src/serl_mock/ids.py
from __future__ import annotations
import csv
import random
import string
from typing import List

def make_alphanumeric_ids_ordered(n: int, length: int = 8, seed: int = 42) -> List[str]:
    """
    Deterministic, ordered, unique uppercase alphanumeric IDs.
    Avoids set->list non-determinism.
    """
    rnd = random.Random(seed)
    chars = string.ascii_uppercase + string.digits
    seen, out = set(), []
    while len(out) < n:
        cand = "".join(rnd.choices(chars, k=length))
        if cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out

def load_puprn_list_csv(path: str) -> List[str]:
    """
    Load a CSV with a 'PUPRN' column; preserves file order and trims whitespace.
    """
    puprns: List[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "PUPRN" not in reader.fieldnames:
            raise ValueError(f"PUPRN column not found in {path}")
        for row in reader:
            v = str(row["PUPRN"]).strip()
            if v:
                puprns.append(v)
    if not puprns:
        raise ValueError(f"No PUPRN values found in {path}")
    return puprns

def write_puprn_list_csv(puprns: List[str], path: str) -> None:
    """
    Write a one-column CSV with header PUPRN.
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["PUPRN"])
        for p in puprns:
            writer.writerow([p])