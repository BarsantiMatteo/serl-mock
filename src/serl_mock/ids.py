# src/serl_mock/ids.py
from __future__ import annotations
import csv
import os
import random
import string
from typing import List, Set, Union

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

def load_puprn_list_csv(path: "Union[str, os.PathLike]") -> List[str]:
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

def write_puprn_list_csv(puprns: List[str], path: "Union[str, os.PathLike]") -> None:
    """
    Write a one-column CSV with header PUPRN.
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["PUPRN"])
        for p in puprns:
            writer.writerow([p])


def select_household_subset(
    puprns: List[str],
    n_selected: int,
    seed: int,
    seed_offset: int,
    label: str,
) -> Set[str]:
    """Return a deterministic subset of households for a device/trait."""
    if n_selected < 0:
        raise ValueError(f"{label} households must be >= 0")
    if n_selected > len(puprns):
        raise ValueError(f"{label} households cannot exceed total number of households")

    rnd = random.Random(seed + seed_offset)
    return set(rnd.sample(puprns, n_selected))


def select_pv_households(puprns: List[str], n_pv: int, seed: int) -> Set[str]:
    """Return a deterministic set of PUPRNs marked as PV households."""
    return select_household_subset(
        puprns=puprns,
        n_selected=n_pv,
        seed=seed,
        seed_offset=600,
        label="pv",
    )