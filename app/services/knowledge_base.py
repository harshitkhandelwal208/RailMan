"""
Offline knowledge retrieval for RailMan AI.

The knowledge base augments the timetable data with FAQs, station notes,
conversation examples, and edge-case guidance so the chatbot can stay useful
when it is running fully offline.

Scoring (v2):
  Token match         +3 per token (exact haystack token)
  Substring match     +1 per token (partial)
  Exact phrase match  +8 (full query in haystack)
  Tag match           +4 per tag that appears in query
  Kind boost          faq +2, station_note +1 (domain-relevant kinds rank higher)
  IDF weight          common tokens get lower bonus (prevents noise inflation)
"""
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

_DATA = Path(__file__).parent.parent / "data"

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "for", "from",
    "get", "give", "has", "have", "how", "i", "if", "in", "is", "it",
    "its", "me", "my", "no", "not", "of", "on", "or", "please", "so",
    "tell", "than", "the", "their", "them", "there", "this", "to", "us",
    "was", "we", "what", "when", "where", "which", "who", "will", "with",
    "you", "your",
}

                                                                     
_KIND_BOOST = {
    "faq":          2,
    "station_note": 1,
    "edge_case":    1,
    "policy":       0,
    "dialogue":     0,
}


def _load_json(filename: str, default):
    path = _DATA / filename
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "item"


def _document_id(item: dict, fallback_field: str = "title") -> str:
    for key in ("id", "slug", fallback_field, "question", "topic", "station"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return _slugify(value)
    return "item"


@lru_cache(maxsize=1)
def get_station_alias_lookup() -> Dict[str, str]:
    raw = _load_json("station_aliases.json", {})
    lookup: Dict[str, str] = {}
    for canonical, aliases in raw.items():
        canonical_name = canonical.strip().lower()
        lookup[canonical_name] = canonical_name
        for alias in aliases:
            lookup[alias.strip().lower()] = canonical_name
    return lookup


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


@lru_cache(maxsize=1)
def _knowledge_documents() -> List[dict]:
    knowledge  = _load_json("chatbot_knowledge.json", {})
    dialogues  = _load_json("chatbot_dialogues.json", [])
    documents: List[dict] = []

    for faq in knowledge.get("faqs", []):
        documents.append({
            "id":   _document_id(faq, "question"),
            "kind": "faq",
            "title": faq["question"],
            "body":  faq["answer"],
            "tags":  faq.get("tags", []),
        })

    for note in knowledge.get("station_notes", []):
        body = " ".join(note.get("notes", []))
        documents.append({
            "id":   _document_id(note, "station"),
            "kind": "station_note",
            "title": note["station"],
            "body":  body,
            "tags":  note.get("tags", []) + [note["station"].lower()],
        })

    for ec in knowledge.get("edge_cases", []):
        documents.append({
            "id":   _document_id(ec, "title"),
            "kind": "edge_case",
            "title": ec["title"],
            "body":  ec["guidance"],
            "tags":  ec.get("tags", []),
        })

    for policy in knowledge.get("response_policies", []):
        documents.append({
            "id":   _document_id(policy, "topic"),
            "kind": "policy",
            "title": policy.get("title") or policy.get("topic", "Policy"),
            "body":  policy.get("guidance", ""),
            "tags":  policy.get("tags", []),
        })

    for example in dialogues:
        documents.append({
            "id":   _document_id(example, "user"),
            "kind": "dialogue",
            "title": example["user"],
            "body":  example["assistant"],
            "tags":  example.get("tags", []),
        })

    return documents


@lru_cache(maxsize=1)
def _build_idf_table() -> Dict[str, float]:
    """
    Compute inverse-document-frequency weights for all tokens in the corpus.
    Tokens that appear in many documents get a lower weight so they don't
    dominate scoring over domain-specific terms.
    """
    docs = _knowledge_documents()
    n = len(docs)
    if n == 0:
        return {}
    df: Dict[str, int] = {}
    for doc in docs:
        haystack = f"{doc.get('title', '')} {doc.get('body', '')} {' '.join(doc.get('tags', []))}"
        for token in set(_tokenize(haystack)):
            df[token] = df.get(token, 0) + 1
    return {token: math.log((n + 1) / (count + 1)) + 1.0 for token, count in df.items()}


def _score_document(query_tokens: List[str], query: str, document: dict) -> float:
    haystack_raw = " ".join([
        document.get("title", ""),
        document.get("body", ""),
        " ".join(document.get("tags", [])),
    ]).lower()
    haystack_tokens = set(_tokenize(haystack_raw))
    idf = _build_idf_table()

    score: float = 0.0

                                         
    for token in query_tokens:
        w = idf.get(token, 1.0)
        if token in haystack_tokens:
            score += 3.0 * w
        elif token in haystack_raw:
            score += 1.0 * w

                        
    lowered_query = query.lower().strip()
    if lowered_query and lowered_query in haystack_raw:
        score += 8.0

                                                                             
    for tag in document.get("tags", []):
        tag_lower = tag.lower().replace("_", " ")
        if tag_lower in lowered_query or tag.lower() in lowered_query:
            score += 4.0

                         
    score += _KIND_BOOST.get(document.get("kind", ""), 0)

    return score


def invalidate_cache() -> None:
    """
    Clear all LRU caches.  Call this after hot-reloading the knowledge JSON
    files (e.g. after an admin API updates the dataset at runtime).
    """
    _knowledge_documents.cache_clear()
    _build_idf_table.cache_clear()
    get_station_alias_lookup.cache_clear()


def search_knowledge(
    query: str,
    limit: int = 4,
    kinds: Optional[List[str]] = None,
    min_score: float = 1.0,
) -> List[dict]:
    """
    Search the offline knowledge corpus and return the top-scoring documents.

    Args:
        query:     Natural-language query string.
        limit:     Maximum number of results.
        kinds:     Restrict to specific document kinds (faq, station_note, …).
        min_score: Documents with score below this threshold are excluded.
    """
    query_tokens = _tokenize(query)
    if not query_tokens and not query.strip():
        return []

    results: List[tuple] = []
    for document in _knowledge_documents():
        if kinds and document["kind"] not in kinds:
            continue
        score = _score_document(query_tokens, query, document)
        if score >= min_score:
            results.append((score, document))

    results.sort(key=lambda item: (-item[0], item[1]["kind"], item[1]["id"]))
    return [doc for _, doc in results[:limit]]


def format_knowledge_context(documents: List[dict], max_items: int = 4) -> str:
    """Format retrieved documents as a compact bullet list for LLM injection."""
    lines = []
    for doc in documents[:max_items]:
        title = doc["title"]
        body  = doc["body"]
        kind  = doc["kind"]
                                                      
        if len(body) > 200:
            body = body[:197] + "…"
        lines.append(f"- [{kind}] {title}: {body}")
    return "\n".join(lines)


def select_dialogue_examples(query: str, limit: int = 2) -> List[dict]:
    return search_knowledge(query, limit=limit, kinds=["dialogue"])


def get_knowledge_stats() -> dict:
    """Return a summary of the loaded knowledge corpus (useful for /health)."""
    docs = _knowledge_documents()
    counts: Dict[str, int] = {}
    for doc in docs:
        counts[doc["kind"]] = counts.get(doc["kind"], 0) + 1
    return {
        "total_documents": len(docs),
        "by_kind": counts,
    }
