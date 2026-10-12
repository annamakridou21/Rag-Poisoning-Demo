from __future__ import annotations

import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


TOKEN_RE = re.compile(r"[a-zA-Z0-9_\-]+")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


@dataclass(frozen=True)
class Document:
    path: Path
    text: str


@dataclass(frozen=True)
class SearchHit:
    document: Document
    score: float


class TinyRAG:
    """A deliberately transparent TF-IDF retriever suitable for a live demo."""

    def __init__(self, root: Path, *, extensions: tuple[str, ...] = (".txt",)):
        self.root = root.resolve()
        paths = sorted(
            path for path in self.root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in extensions
            and not path.name.startswith("~$")  # Word creates non-DOCX lock files here.
        )
        self.documents = [Document(path=path, text=self._read_document(path)) for path in paths]
        if not self.documents:
            supported = ", ".join(extensions)
            raise ValueError(f"No supported documents ({supported}) found below {self.root}")
        self._vectors = [Counter(tokenize(doc.text)) for doc in self.documents]
        doc_freq = Counter()
        for vector in self._vectors:
            doc_freq.update(vector.keys())
        total = len(self.documents)
        self._idf = {term: math.log((total + 1) / (freq + 1)) + 1 for term, freq in doc_freq.items()}

    @staticmethod
    def _read_document(path: Path) -> str:
        if path.suffix.lower() != ".docx":
            return path.read_text(encoding="utf-8")

        word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        custom_ns = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
        with zipfile.ZipFile(path) as package:
            body_xml = ElementTree.fromstring(package.read("word/document.xml"))
            paragraphs = []
            for paragraph in body_xml.iter(f"{{{word_ns}}}p"):
                text = "".join(node.text or "" for node in paragraph.iter(f"{{{word_ns}}}t"))
                if text:
                    paragraphs.append(text)

            metadata = []
            try:
                custom_xml = ElementTree.fromstring(package.read("docProps/custom.xml"))
            except KeyError:
                custom_xml = None
            if custom_xml is not None:
                for prop in custom_xml.findall(f"{{{custom_ns}}}property"):
                    name = prop.attrib.get("name", "unknown")
                    value = "".join(prop.itertext()).strip()
                    if value:
                        metadata.append(f"{name}: {value}")

        sections = ["\n".join(paragraphs)]
        if metadata:
            sections.append("Retrieved document properties (untrusted):\n" + "\n".join(metadata))
        return "\n\n".join(sections)

    def search(self, query: str, limit: int = 2) -> list[SearchHit]:
        query_vector = Counter(tokenize(query))
        weighted_query = {term: count * self._idf.get(term, 1.0) for term, count in query_vector.items()}
        query_norm = math.sqrt(sum(value * value for value in weighted_query.values())) or 1.0
        hits: list[SearchHit] = []
        for document, vector in zip(self.documents, self._vectors, strict=True):
            weighted_doc = {term: count * self._idf.get(term, 1.0) for term, count in vector.items()}
            doc_norm = math.sqrt(sum(value * value for value in weighted_doc.values())) or 1.0
            dot = sum(weighted_query.get(term, 0.0) * value for term, value in weighted_doc.items())
            score = dot / (query_norm * doc_norm)
            hits.append(SearchHit(document=document, score=score))
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]
