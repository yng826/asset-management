import os

import mariadb

from config.settings import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER


class DBConnectionError(RuntimeError):
    """strict 조회에서 DB 연결 실패 (빈 결과와 구분용)."""


def get_connection():
    """MariaDB 커넥션 객체 생성 및 반환"""
    try:
        conn = mariadb.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, autocommit=True
        )
        return conn
    except mariadb.Error as e:
        print(f"❌ DB 연결 실패: {e}")
        return None


def fetch_all(query: str, params: tuple = (), strict: bool = False) -> list:
    """SELECT 결과 행 튜플 리스트. 연결 실패 시 빈 리스트 (strict=True 면 DBConnectionError). 쿼리 오류는 그대로 전파."""
    conn = get_connection()
    if not conn:
        if strict:
            raise DBConnectionError("DB 연결 실패")
        return []
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def execute(query: str, params: tuple = ()) -> bool:
    """INSERT/UPDATE/DELETE/DDL 실행 (autocommit). 연결 실패 시 실행하지 않고 False. 쿼리 오류는 그대로 전파."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        cur.close()
        return True
    finally:
        conn.close()


def execute_many(query: str, rows: list) -> bool:
    """같은 문장을 여러 행에 일괄 실행 (executemany, autocommit). 연결 실패 시 False. 쿼리 오류는 그대로 전파."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.executemany(query, rows)
        cur.close()
        return True
    finally:
        conn.close()


def read_df(query: str, params: tuple = (), strict: bool = False):
    """SELECT 결과 pandas DataFrame. 연결 실패 시 빈 DataFrame (strict=True 면 DBConnectionError). 쿼리 오류는 그대로 전파."""
    import warnings

    import pandas as pd

    conn = get_connection()
    if not conn:
        if strict:
            raise DBConnectionError("DB 연결 실패")
        return pd.DataFrame()
    try:
        # pandas 는 SQLAlchemy 가 아닌 DBAPI 커넥션에 UserWarning 을 내므로 무시
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def test_connection():
    """연결 테스트용 헬퍼 함수"""
    conn = get_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES;")
        tables = cursor.fetchall()
        print("✅ DB 연결 성공! 현재 테이블 목록:")
        for table in tables:
            print(f" - {table[0]}")
        cursor.close()
        conn.close()
        return True
    return False


def init_tables():
    """schema.sql 파일을 읽어 MariaDB에 테이블 직접 생성"""
    conn = get_connection()
    if not conn:
        print("❌ DB 연결 실패")
        return

    sql_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql_commands = f.read()

    cursor = conn.cursor()
    # 세미콜론(;) 기준으로 쿼리 분리 실행
    for statement in sql_commands.split(";"):
        # 줄 단위 주석(--) 제거 후 실제 쿼리만 실행 (주석 뒤에 붙은 쿼리가 통째로 스킵되지 않도록)
        lines = [line for line in statement.splitlines() if not line.strip().startswith("--")]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            try:
                cursor.execute(cleaned)
            except mariadb.Error as e:
                print(f"⚠️ 쿼리 실행 경고: {e}")

    print("🚀 테이블 생성 완료!")

    # 생성 확인
    cursor.execute("SHOW TABLES;")
    for table in cursor.fetchall():
        print(f" - {table[0]}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    init_tables()
