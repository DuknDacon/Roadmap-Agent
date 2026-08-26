import sqlite3
from pathlib import Path

from roadmap_agent.conversation_store import SqliteConversationStore
from roadmap_agent.repositories import sqlite_connection_factory


def test_structured_data_and_conversation_tables_share_one_sqlite_file(tmp_path):
    db_path = tmp_path / "seedup.sqlite"
    schema = Path(__file__).parents[1] / "db" / "sqlite_schema.sql"
    connection = sqlite3.connect(db_path)
    connection.executescript(schema.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO finlife_saving_base "
        "(dcls_month, fin_co_no, fin_prdt_cd, kor_co_nm, fin_prdt_nm) "
        "VALUES ('202608', '001', 'A', '테스트은행', '공용적금')"
    )
    connection.commit()
    connection.close()

    store = SqliteConversationStore(db_path)
    store.touch("thread-1", now=100)

    with sqlite_connection_factory(db_path)() as reader:
        assert reader.execute("SELECT count(*) FROM finlife_saving_base").fetchone()[0] == 1
        assert reader.execute(
            "SELECT count(*) FROM conversation_sessions WHERE thread_id = ?",
            ("thread-1",),
        ).fetchone()[0] == 1
    store.close()
