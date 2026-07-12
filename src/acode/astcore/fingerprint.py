"""Deterministic structural AST fingerprints.

A fingerprint is a fixed-dimension vector built by feature-hashing the
named-node structure of a syntax tree (node-type unigrams plus
parent>child bigrams). No embedding model is involved: the same code
always yields the same vector, so retrieval ranking is reproducible and
auditable. Identifiers and literal values are deliberately excluded so
that two snippets with the same *shape* (e.g. the same decorator/class
pattern with different names) land close together.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter

from tree_sitter import Node

from .parser import parse

DIM = 256


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.md5(feature.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % DIM
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign


def _features(root: Node) -> Counter:
    counts: Counter = Counter()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_named:
            counts[node.type] += 1
        for child in node.children:
            if child.is_named:
                if node.is_named:
                    counts[f"{node.type}>{child.type}"] += 1
                stack.append(child)
            else:
                stack.append(child)
    return counts


def fingerprint_node(root: Node) -> list[float]:
    vec = [0.0] * DIM
    for feature, count in _features(root).items():
        index, sign = _bucket(feature)
        # sublinear tf so one huge node type does not drown the shape
        vec[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def fingerprint_code(code: str, language: str) -> list[float]:
    return fingerprint_node(parse(code, language).root_node)


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("fingerprint dimensions differ")
    return sum(x * y for x, y in zip(a, b))
