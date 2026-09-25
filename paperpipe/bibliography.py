"""Reference-manager exports: BibTeX, CSL-JSON and RIS.

Each paper becomes one entry carrying title, authors, year, DOI, arXiv id and URL,
in a shape Zotero (and most other reference managers) imports directly. Output is
sorted by citation key so re-exporting an unchanged corpus gives identical files.

The ``arxiv_id`` column is really the paper's key: non-arXiv discoveries are stored
as ``doi:<doi>`` or a source-specific id, so the arXiv id and DOI are derived here
rather than read verbatim.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

# 2401.00001 / 2401.00001v2 (new style), hep-th/9901001 / math.GT/0309136 (old style)
ARXIV_ID_RE = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")

# Characters that break a BibTeX field or LaTeX when unescaped. Braces and ``$`` are
# left alone so arXiv's inline LaTeX survives; unbalanced braces are stripped instead.
BIBTEX_ESCAPES = {"&": r"\&", "%": r"\%", "#": r"\#"}


def arxiv_id(paper: Dict[str, object]) -> Optional[str]:
    key = str(paper.get("arxiv_id") or "")
    return key if ARXIV_ID_RE.match(key) else None


def doi(paper: Dict[str, object]) -> Optional[str]:
    value = paper.get("doi")
    if value:
        return str(value)
    key = str(paper.get("arxiv_id") or "")
    return key[4:] if key.startswith("doi:") else None


def url(paper: Dict[str, object]) -> Optional[str]:
    if paper.get("abs_url"):
        return str(paper["abs_url"])
    found = doi(paper)
    return f"https://doi.org/{found}" if found else None


def year(paper: Dict[str, object]) -> Optional[str]:
    published = str(paper.get("published") or "")
    return published[:4] if published[:4].isdigit() else None


def _date_parts(paper: Dict[str, object]) -> List[int]:
    parts = []
    for piece in str(paper.get("published") or "")[:10].split("-"):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return parts


def split_name(name: str) -> Tuple[Optional[str], str]:
    """``(given, family)`` from "Given Family" or "Family, Given"; given is None for mononyms."""
    name = " ".join(name.split())
    if "," in name:
        family, given = (part.strip() for part in name.split(",", 1))
        return (given or None), family
    pieces = name.rsplit(" ", 1)
    if len(pieces) == 1:
        return None, name
    return pieces[0], pieces[1]


def _ascii_word(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def _base_key(paper: Dict[str, object]) -> str:
    authors = paper.get("authors") or []
    family = _ascii_word(split_name(str(authors[0]))[1]) if authors else ""
    words = [_ascii_word(w) for w in str(paper.get("title") or "").split()]
    first_word = next((w for w in words if len(w) > 3), next((w for w in words if w), ""))
    return (family or "anon") + (year(paper) or "nd") + first_word


def keyed(papers: List[Dict[str, object]]) -> List[Tuple[str, Dict[str, object]]]:
    """Unique, deterministic citation keys (``smith2024attention``, then ``...b``, ``...c``)."""
    ordered = sorted(papers, key=lambda p: (_base_key(p), str(p.get("arxiv_id") or "")))
    seen: Dict[str, int] = {}
    out = []
    for paper in ordered:
        base = _base_key(paper)
        count = seen.get(base, 0)
        seen[base] = count + 1
        out.append((base + (chr(ord("a") + count) if count else ""), paper))
    return out


def _bibtex_value(text: str) -> str:
    text = " ".join(str(text).split())
    text = "".join(BIBTEX_ESCAPES.get(ch, ch) for ch in text)
    depth = 0
    for ch in text:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth < 0:
            break
    if depth != 0:
        text = text.replace("{", "").replace("}", "")
    return text


def _bibtex_author(name: str) -> str:
    # BibTeX splits the author list on " and "; brace a name that contains one.
    value = _bibtex_value(name)
    return "{" + value + "}" if re.search(r"\sand\s", value, re.IGNORECASE) else value


def to_bibtex(papers: List[Dict[str, object]]) -> str:
    entries = []
    for key, paper in keyed(papers):
        eprint = arxiv_id(paper)
        journal = paper.get("journal_ref")
        fields: List[Tuple[str, Optional[str]]] = [
            ("title", "{" + _bibtex_value(paper.get("title") or "") + "}"),
            ("author", " and ".join(_bibtex_author(str(a)) for a in paper.get("authors") or []) or None),
            ("year", year(paper)),
            ("journal", _bibtex_value(journal) if journal else None),
            ("doi", doi(paper)),
            ("eprint", eprint),
            ("archiveprefix", "arXiv" if eprint else None),
            ("primaryclass", paper.get("primary_category") if eprint else None),
            ("url", url(paper)),
            ("abstract", _bibtex_value(paper["abstract"]) if paper.get("abstract") else None),
        ]
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value)
        entries.append(f"@{'article' if journal else 'misc'}{{{key},\n{body}\n}}\n")
    return "\n".join(entries)


def _csl_author(name: str) -> Dict[str, str]:
    given, family = split_name(name)
    return {"family": family, "given": given} if given else {"literal": family}


def to_csljson(papers: List[Dict[str, object]]) -> str:
    items = []
    for key, paper in keyed(papers):
        eprint = arxiv_id(paper)
        journal = paper.get("journal_ref")
        item: Dict[str, object] = {
            "id": key,
            "type": "article-journal" if journal else "article",
            "title": " ".join(str(paper.get("title") or "").split()),
            "author": [_csl_author(str(a)) for a in paper.get("authors") or []],
        }
        parts = _date_parts(paper)
        if parts:
            item["issued"] = {"date-parts": [parts]}
        if journal:
            item["container-title"] = journal
        elif eprint:
            item["publisher"] = "arXiv"
        if eprint:
            item["number"] = f"arXiv:{eprint}"
        optional = {"DOI": doi(paper), "URL": url(paper), "abstract": paper.get("abstract")}
        item.update({k: v for k, v in optional.items() if v})
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False) + "\n"


def to_ris(papers: List[Dict[str, object]]) -> str:
    records = []
    for _key, paper in keyed(papers):
        eprint = arxiv_id(paper)
        journal = paper.get("journal_ref")
        lines = [("TY", "JOUR" if journal else "GEN"),
                 ("TI", " ".join(str(paper.get("title") or "").split()))]
        lines += [("AU", " ".join(str(a).split())) for a in paper.get("authors") or []]
        parts = _date_parts(paper)
        if parts:
            lines.append(("PY", str(parts[0])))
            lines.append(("DA", "/".join(f"{p:02d}" for p in parts) + "/"))
        if journal:
            lines.append(("JO", str(journal)))
        for tag, value in (("DO", doi(paper)), ("UR", url(paper))):
            if value:
                lines.append((tag, value))
        if eprint:
            lines.append(("AN", f"arXiv:{eprint}"))
        if paper.get("abstract"):
            lines.append(("AB", " ".join(str(paper["abstract"]).split())))
        lines.append(("ER", ""))
        records.append("\n".join(f"{tag}  - {value}" for tag, value in lines))
    return "\n\n".join(records) + ("\n" if records else "")
