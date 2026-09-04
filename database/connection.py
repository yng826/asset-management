import mariadb
import os
from config.settings import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

def get_connection():
    """MariaDB 커넥션 객체 생성 및 반환"""
    try:
        conn = mariadb.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            autocommit=True
        )
        return conn
    except mariadb.Error as e:
        print(f"❌ DB 연결 실패: {e}")
        return None

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
    with open(sql_path, "r", encoding="utf-8") as f:
        sql_commands = f.read()

    cursor = conn.cursor()
    # 세미콜론(;) 기준으로 쿼리 분리 실행
    for statement in sql_commands.split(";"):
        cleaned = statement.strip()
        # 주석이나 빈 줄 제외하고 실제 쿼리만 실행
        if cleaned and not cleaned.startswith("--"):
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