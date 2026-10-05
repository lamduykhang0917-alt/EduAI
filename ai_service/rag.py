import json
import math
import os
import sqlite3
from collections import Counter
from contextlib import contextmanager

from . import config
from .preprocessing import clean_text, STOPWORDS

DB_PATH = os.path.join(config.BASE_DIR, "backend", "eduai.db")


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _tokenize(text: str) -> list:
    cleaned = clean_text(text)
    return [t for t in cleaned.split() if t not in STOPWORDS and len(t) > 1]


def _term_freq(text: str) -> Counter:
    return Counter(_tokenize(text))


def _load_knowledge_items():
    with open(config.KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("knowledge_base", [])


def _chunk_text(item: dict) -> list:
    basic = item.get("answer_basic", "").strip()
    detail = item.get("answer_detailed", "").strip()
    example = item.get("example", "").strip()
    chunks = []
    if basic:
        chunks.append(f"{item.get('topic', '')}: {basic}")
    if detail:
        tail = f" {example}" if example else ""
        chunks.append(f"{item.get('topic', '')}: {detail}{tail}")
    return chunks


def _find_document_id(conn, course_name: str, chapter_name: str):
    row = conn.execute(
        "SELECT d.id FROM documents d "
        "JOIN courses c ON d.course_id = c.id "
        "LEFT JOIN chapters ch ON d.chapter_id = ch.id "
        "WHERE c.name = ? AND (ch.name = ? OR d.chapter_id IS NULL) "
        "ORDER BY (ch.name = ?) DESC LIMIT 1",
        (course_name, chapter_name, chapter_name),
    ).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "SELECT d.id FROM documents d JOIN courses c ON d.course_id = c.id "
        "WHERE c.name = ? LIMIT 1",
        (course_name,),
    ).fetchone()
    if row:
        return row["id"]
    return _create_knowledge_document(conn, course_name, chapter_name)


def _create_knowledge_document(conn, course_name: str, chapter_name: str):
    course_row = conn.execute("SELECT id FROM courses WHERE name = ?", (course_name,)).fetchone()
    if course_row is None:
        return None
    course_id = course_row["id"]
    chapter_row = conn.execute(
        "SELECT id FROM chapters WHERE course_id = ? AND name = ?", (course_id, chapter_name)
    ).fetchone()
    chapter_id = chapter_row["id"] if chapter_row else None
    title = f"Nội dung tổng hợp tri thức: {chapter_name}" if chapter_name else f"Nội dung tổng hợp tri thức: {course_name}"
    existing = conn.execute(
        "SELECT id FROM documents WHERE course_id = ? AND title = ?", (course_id, title)
    ).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO documents (course_id, chapter_id, title, file_type, file_path, status) "
        "VALUES (?, ?, ?, 'dataset', '', 'active')",
        (course_id, chapter_id, title),
    )
    return cur.lastrowid


def build_index() -> dict:
    items = _load_knowledge_items()
    inserted = 0
    skipped = 0
    with _connect() as conn:
        conn.execute("DELETE FROM document_chunks WHERE page_hint LIKE 'kb%' OR page_hint = '' OR page_hint IS NULL")
        for item in items:
            document_id = _find_document_id(conn, item.get("course", ""), item.get("chapter", ""))
            if document_id is None:
                skipped += 1
                continue
            for idx, chunk_content in enumerate(_chunk_text(item)):
                tf = _term_freq(chunk_content)
                conn.execute(
                    "INSERT INTO document_chunks (document_id, content, chunk_index, "
                    "vector_embedding, page_hint) VALUES (?, ?, ?, ?, ?)",
                    (document_id, chunk_content, idx, json.dumps(tf), item.get("id", "")),
                )
                inserted += 1
    return {"chunks_indexed": inserted, "knowledge_items_skipped": skipped,
            "total_knowledge_items": len(items)}


def _cosine(vec_a: dict, idf_a_terms: set, vec_b: dict) -> float:
    common = set(vec_a) & set(vec_b)
    if not common:
        return 0.0
    dot = sum(vec_a[t] * vec_b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search(query: str, course: str = None, top_k: int = 3) -> list:
    with _connect() as conn:
        sql = (
            "SELECT dc.id, dc.content, dc.vector_embedding, dc.page_hint, "
            "d.title as document_title, c.name as course_name, ch.name as chapter_name "
            "FROM document_chunks dc "
            "JOIN documents d ON dc.document_id = d.id "
            "JOIN courses c ON d.course_id = c.id "
            "LEFT JOIN chapters ch ON d.chapter_id = ch.id"
        )
        params = []
        if course:
            sql += " WHERE c.name = ?"
            params.append(course)
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return []

    doc_freq = Counter()
    chunk_tfs = []
    for row in rows:
        tf = json.loads(row["vector_embedding"]) if row["vector_embedding"] else {}
        chunk_tfs.append(tf)
        for term in tf:
            doc_freq[term] += 1

    n_docs = len(rows)
    idf = {term: math.log((1 + n_docs) / (1 + df)) + 1 for term, df in doc_freq.items()}

    query_tf = _term_freq(query)
    query_vec = {term: freq * idf.get(term, math.log(1 + n_docs) + 1) for term, freq in query_tf.items()}

    scored = []
    for row, tf in zip(rows, chunk_tfs):
        chunk_vec = {term: freq * idf.get(term, 0) for term, freq in tf.items()}
        score = _cosine(query_vec, set(query_vec), chunk_vec)
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, row in scored[:top_k]:
        results.append({
            "chunk_id": row["id"],
            "content": row["content"],
            "document_title": row["document_title"],
            "course": row["course_name"],
            "chapter": row["chapter_name"],
            "score": round(score, 4),
        })
    return results


def index_stats() -> dict:
    with _connect() as conn:
        total_chunks = conn.execute("SELECT COUNT(*) c FROM document_chunks").fetchone()["c"]
        total_documents = conn.execute(
            "SELECT COUNT(DISTINCT document_id) c FROM document_chunks"
        ).fetchone()["c"]
    return {"total_chunks": total_chunks, "documents_indexed": total_documents}
