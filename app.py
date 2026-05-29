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

@dataclass
class BookItem:
    id: int
    book_id: int
    inventory_number: str
    status: str          # 'Доступен' или 'Выдан'
    issue_date: str = "" # Дата выдачи, если статус 'Выдан'


def init_db() -> None:
    with get_connection() as conn:
        # 1. Создаем основную таблицу книг, если её нет
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

        # 2. Создаем таблицу экземпляров, если её нет
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS book_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                inventory_number TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'Доступен',
                issue_date TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE
            )
            """
        )

        # 3. МИГРАЦИЯ ДЛЯ СТАРЫХ КНИГ: досоздаем экземпляры, если их не хватает
        books = conn.execute("SELECT id, copies FROM books").fetchall()
        for book in books:
            book_id = book["id"]
            total_copies = book["copies"]

            # Считаем, сколько экземпляров уже записано в базе для этой книги
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM book_items WHERE book_id = ?", (book_id,)
            ).fetchone()
            existing_count = row["cnt"] if row else 0

            # Если в новой таблице пусто или записей меньше, чем указано в copies
            if existing_count < total_copies:
                for i in range(existing_count + 1, total_copies + 1):
                    # Генерируем красивый инвентарный номер: например, КН-0002-01
                    inventory_number = f"КН-{book_id:04d}-{i:02d}"
                    try:
                        conn.execute(
                            """
                            INSERT INTO book_items (book_id, inventory_number, status, issue_date)
                            VALUES (?, ?, 'Доступен', '')
                            """,
                            (book_id, inventory_number)
                        )
                    except sqlite3.IntegrityError:
                        # Защита на случай, если номер уже случайно существовал
                        pass

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

    title = request.form.get("title", "").strip()
    author = request.form.get("author", "").strip()
    year = parse_int(request.form.get("year", ""))
    genre = resolve_genre_from_form()
    copies = max(0, parse_int(request.form.get("copies", "0")))
    description = request.form.get("description", "").strip()

    with get_connection() as conn:
        # 1. Сохраняем саму книгу
        cursor = conn.execute(
            """
            INSERT INTO books (title, author, year, genre, copies, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title, author, year, genre, copies, description),
        )
        # Получаем ID только что созданной книги
        book_id = cursor.lastrowid

        # 2. АВТОМАТИЧЕСКИ создаем физические экземпляры для этой книги
        for i in range(1, copies + 1):
            # Генерируем уникальный инвентарный номер, например: КН-0014-01
            inventory_number = f"КН-{book_id:04d}-{i:02d}"
            conn.execute(
                """
                INSERT INTO book_items (book_id, inventory_number, status, issue_date)
                VALUES (?, ?, 'Доступен', '')
                """,
                (book_id, inventory_number)
            )

    return redirect(url_for("index"))

def get_book_or_none(book_id: int) -> Optional[Book]:
    return next((book for book in load_books() if book.id == book_id), None)


@app.get("/books/<int:book_id>")
def view_book(book_id: int):
    book = get_book_or_none(book_id)
    if not book:
        return redirect(url_for("index"))

    # Загружаем экземпляры этой книги из новой таблицы
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, book_id, inventory_number, status, issue_date FROM book_items WHERE book_id = ?",
            (book.id,)
        ).fetchall()

    items = [
        BookItem(
            id=row["id"],
            book_id=row["book_id"],
            inventory_number=row["inventory_number"],
            status=row["status"],
            issue_date=row["issue_date"]
        ) for row in rows
    ]

    # Считаем, сколько реально доступно на основе статусов в базе
    available_copies = sum(1 for item in items if item.status == 'Доступен')

    return render_template(
        "book.html",
        book=book,
        items=items,  # Передаем список экземпляров в HTML
        available_copies=available_copies,  # Передаем динамический счетчик
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
        # ИСПРАВЛЕНИЕ: Догружаем реальные экземпляры для модального режима редактирования
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, book_id, inventory_number, status, issue_date FROM book_items WHERE book_id = ?",
                (book.id,)
            ).fetchall()

        items = [
            BookItem(
                id=row["id"],
                book_id=row["book_id"],
                inventory_number=row["inventory_number"],
                status=row["status"],
                issue_date=row["issue_date"]
            ) for row in rows
        ]

        available_copies = sum(1 for item in items if item.status == 'Доступen')
        issued_copies = sum(1 for item in items if item.status == 'Выдан')

        return render_template(
            "book.html",
            book=book,
            items=items,
            available_copies=available_copies,
            issued_copies=issued_copies,
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
        # ИСПРАВЛЕНИЕ: Догружаем реальные экземпляры для модального режима удаления
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, book_id, inventory_number, status, issue_date FROM book_items WHERE book_id = ?",
                (book.id,)
            ).fetchall()

        items = [
            BookItem(
                id=row["id"],
                book_id=row["book_id"],
                inventory_number=row["inventory_number"],
                status=row["status"],
                issue_date=row["issue_date"]
            ) for row in rows
        ]

        available_copies = sum(1 for item in items if item.status == 'Доступен')
        issued_copies = sum(1 for item in items if item.status == 'Выдан')

        return render_template(
            "book.html",
            book=book,
            items=items,  # Передаем реальные экземпляры
            available_copies=available_copies,  # Передаем счетчик доступных
            issued_copies=issued_copies,  # Передаем счетчик выданных
            genres=GENRES,
            genre_other=GENRE_OTHER,
            delete_book=book,  # Сигнал для открытия модалки
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


from datetime import datetime


@app.post("/items/<int:item_id>/toggle-status")
def toggle_item_status(item_id: int):
    """Переключает статус конкретного экземпляра книги."""
    init_db()
    with get_connection() as conn:
        # 1. Получаем текущий статус и ID книги, чтобы знать, куда вернуться
        item = conn.execute(
            "SELECT book_id, status FROM book_items WHERE id = ?", (item_id,)
        ).fetchone()

        if not item:
            return redirect(url_for("index"))

        book_id = item["book_id"]

        # 2. Определяем новый статус и дату выдачи
        if item["status"] == "Доступен":
            new_status = "Выдан"
            # Записываем текущую дату в понятном формате, например: "28.05.2026"
            new_date = datetime.now().strftime("%d.%m.%Y")
        else:
            new_status = "Доступен"
            new_date = ""

        # 3. Обновляем запись в базе данных
        conn.execute(
            """
            UPDATE book_items 
            SET status = ?, issue_date = ? 
            WHERE id = ?
            """,
            (new_status, new_date, item_id)
        )

    # Возвращаем пользователя обратно на страницу этой же книги
    return redirect(url_for("view_book", book_id=book_id))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
