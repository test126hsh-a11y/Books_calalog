from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)
DB_FILE = Path("data") / "books.db"

GENRES = [
    "Роман",
    "Фантастика",
    "Детектив",
    "История",
    "Реализм",
    "Приключения",
    "Поэзия",
    "Антиутопия",
]
GENRE_OTHER = "Другое"

SORT_OPTIONS: List[Tuple[str, str]] = [
    ("title_asc", "По названию(А-Я)"),
    ("title_desc", "По названию(Я-А)"),
    ("author_asc", "По автору(А-Я)"),
    ("author_desc", "По автору(Я-А)"),
    ("year_asc", "По году(стар-нов)"),
    ("year_desc", "По году(нов-стар)"),
]


@dataclass
class Book:
    id: int
    title: str
    author: str
    year: int
    genre: str
    copies: int
    description: str = ""


def _ensure_data_dir() -> None:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    _ensure_data_dir()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_description_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(books)")}
    if "description" not in columns:
        conn.execute("ALTER TABLE books ADD COLUMN description TEXT NOT NULL DEFAULT ''")


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                year INTEGER NOT NULL,
                genre TEXT NOT NULL,
                copies INTEGER NOT NULL CHECK (copies >= 0),
                description TEXT NOT NULL DEFAULT ''
            )
            """
        )
        _ensure_description_column(conn)


def load_books() -> List[Book]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, author, year, genre, copies, description FROM books"
        ).fetchall()
    return [
        Book(
            id=row["id"],
            title=row["title"],
            author=row["author"],
            year=row["year"],
            genre=row["genre"],
            copies=row["copies"],
            description=row["description"] or "",
        )
        for row in rows
    ]


def parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_sort(sort_key: str) -> Tuple[str, str]:
    mapping = {
        "title_asc": ("title", "asc"),
        "title_desc": ("title", "desc"),
        "author_asc": ("author", "asc"),
        "author_desc": ("author", "desc"),
        "year_asc": ("year", "asc"),
        "year_desc": ("year", "desc"),
    }
    return mapping.get(sort_key, ("title", "asc"))


def resolve_genre_from_form() -> str:
    genre = request.form.get("genre", "").strip()
    if genre == GENRE_OTHER:
        custom = request.form.get("genre_other", "").strip()
        return custom or GENRE_OTHER
    return genre


def genre_for_form(book_genre: str) -> Tuple[str, str]:
    if book_genre in GENRES:
        return book_genre, ""
    if not book_genre:
        return "", ""
    return GENRE_OTHER, book_genre


def apply_filters_and_sort(
    books: List[Book],
    search_q: str,
    author_q: str,
    genre_q: str,
    year_q: str,
    sort_by: str,
    sort_order: str,
) -> List[Book]:
    filtered = books

    if search_q:
        q = search_q.lower()
        filtered = [
            book
            for book in filtered
            if q in book.title.lower() or q in book.author.lower() or q in book.genre.lower()
        ]
    if author_q:
        filtered = [book for book in filtered if book.author == author_q]
    if genre_q:
        if genre_q == GENRE_OTHER:
            filtered = [book for book in filtered if book.genre not in GENRES]
        else:
            filtered = [book for book in filtered if book.genre == genre_q]
    if year_q:
        year_val = parse_int(year_q, -1)
        if year_val >= 0:
            filtered = [book for book in filtered if book.year == year_val]

    key_map = {
        "title": lambda b: b.title.lower(),
        "author": lambda b: b.author.lower(),
        "year": lambda b: b.year,
        "genre": lambda b: b.genre.lower(),
        "copies": lambda b: b.copies,
    }
    sort_key = key_map.get(sort_by, key_map["title"])
    reverse = sort_order == "desc"
    return sorted(filtered, key=sort_key, reverse=reverse)


def build_index_context(args, extra: dict | None = None) -> dict:
    books = load_books()
    search_q = args.get("q", "").strip()
    author_q = args.get("author", "").strip()
    genre_q = args.get("genre", "").strip()
    year_q = args.get("year", "").strip()
    sort_param = args.get("sort", "title_asc")
    sort_by, sort_order = parse_sort(sort_param)

    books_view = apply_filters_and_sort(
        books,
        search_q=search_q,
        author_q=author_q,
        genre_q=genre_q,
        year_q=year_q,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    per_page = 5
    page = max(1, parse_int(args.get("page", "1"), 1))
    total_books = len(books_view)
    total_pages = max(1, (total_books + per_page - 1) // per_page)
    page = min(page, total_pages)
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_books = books_view[start_idx:end_idx]

    authors = sorted({book.author for book in books})

    context = {
        "books": paginated_books,
        "genres": GENRES,
        "genre_other": GENRE_OTHER,
        "authors": authors,
        "sort_options": SORT_OPTIONS,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_books": total_books,
            "total_pages": total_pages,
            "start": start_idx + 1 if total_books else 0,
            "end": min(end_idx, total_books),
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
        "filters": {
            "q": search_q,
            "author": author_q,
            "genre": genre_q,
            "year": year_q,
            "sort": sort_param,
        },
    }
    if extra:
        context.update(extra)
    return context


@app.get("/")
def index():
    return render_template("index.html", **build_index_context(request.args))


@app.get("/books/new")
def new_book():
    return render_template(
        "form.html",
        mode="create",
        book=None,
        genres=GENRES,
        genre_other=GENRE_OTHER,
        form_genre="",
        form_genre_other="",
    )


@app.post("/books")
def create_book():
    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO books (title, author, year, genre, copies, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("title", "").strip(),
                request.form.get("author", "").strip(),
                parse_int(request.form.get("year", "")),
                resolve_genre_from_form(),
                max(0, parse_int(request.form.get("copies", "0"))),
                request.form.get("description", "").strip(),
            ),
        )
    return redirect(url_for("index"))


def get_book_or_none(book_id: int) -> Optional[Book]:
    return next((book for book in load_books() if book.id == book_id), None)


@app.get("/books/<int:book_id>")
def view_book(book_id: int):
    book = get_book_or_none(book_id)
    if not book:
        return redirect(url_for("index"))
    return render_template(
        "book.html",
        book=book,
        genres=GENRES,
        genre_other=GENRE_OTHER,
    )


@app.get("/books/<int:book_id>/edit")
def edit_book(book_id: int):
    book = get_book_or_none(book_id)
    if not book:
        return redirect(url_for("index"))
    form_genre, form_genre_other = genre_for_form(book.genre)
    from_view = request.args.get("from") == "view"
    modal_ctx = {
        "modal_mode": "edit",
        "modal_book": book,
        "form_genre": form_genre,
        "form_genre_other": form_genre_other,
    }
    if from_view:
        return render_template(
            "book.html",
            book=book,
            genres=GENRES,
            genre_other=GENRE_OTHER,
            **modal_ctx,
        )
    return render_template(
        "index.html",
        **build_index_context(request.args, modal_ctx),
    )


@app.get("/books/<int:book_id>/delete")
def confirm_delete_book(book_id: int):
    book = get_book_or_none(book_id)
    if not book:
        return redirect(url_for("index"))
    from_view = request.args.get("from") == "view"
    if from_view:
        return render_template(
            "book.html",
            book=book,
            genres=GENRES,
            genre_other=GENRE_OTHER,
            delete_book=book,
        )
    return render_template(
        "index.html",
        **build_index_context(request.args, {"delete_book": book}),
    )


@app.post("/books/<int:book_id>/update")
def update_book(book_id: int):
    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE books
            SET title = ?, author = ?, year = ?, genre = ?, copies = ?, description = ?
            WHERE id = ?
            """,
            (
                request.form.get("title", "").strip(),
                request.form.get("author", "").strip(),
                parse_int(request.form.get("year", "0")),
                resolve_genre_from_form(),
                max(0, parse_int(request.form.get("copies", "0"))),
                request.form.get("description", "").strip(),
                book_id,
            ),
        )
    if request.form.get("return_to") == "view":
        return redirect(url_for("view_book", book_id=book_id))
    return redirect(url_for("index"))


@app.post("/books/<int:book_id>/delete")
def delete_book(book_id: int):
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
