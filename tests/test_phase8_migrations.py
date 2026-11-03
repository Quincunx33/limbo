"""Phase 8 SQLite migrations and database URL policy."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from limbo.database import Database
from limbo.migrations import DatabaseURLError, Migration, MigrationError, Migrator, load_directory, open_database


class MigrationTests(unittest.TestCase):
    def test_apply_is_idempotent_and_checksum_is_enforced(self):
        database = Database(":memory:")
        migrator = Migrator(database)
        migrations = [
            Migration("001", "users", "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"),
            Migration("002", "seed", "INSERT INTO users(name) VALUES ('ada')", "DELETE FROM users"),
        ]
        self.assertEqual(migrator.apply(migrations), ["001", "002"])
        self.assertEqual(migrator.apply(migrations), [])
        row = database.execute("SELECT name FROM users").fetchone()
        self.assertEqual(row["name"], "ada")
        changed = [migrations[0], Migration("002", "seed", "INSERT INTO users(name) VALUES ('changed')", "DELETE FROM users")]
        with self.assertRaisesRegex(MigrationError, "Checksum"):
            migrator.apply(changed)

    def test_rollback_requires_down_sql(self):
        database = Database(":memory:")
        migrator = Migrator(database)
        migrations = [Migration("001", "users", "CREATE TABLE users (id INTEGER)")]
        migrator.apply(migrations)
        with self.assertRaisesRegex(MigrationError, "no down SQL"):
            migrator.rollback(migrations)
        migrations = [Migration("001", "users", "CREATE TABLE users (id INTEGER)", "DROP TABLE users")]
        self.assertEqual(migrator.rollback(migrations), ["001"])
        with self.assertRaises(Exception):
            database.execute("SELECT * FROM users")

    def test_directory_loader_and_failure_rolls_back(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "001_notes.sql"
            path.write_text("-- +limbo Up\nCREATE TABLE notes (id INTEGER);\n-- +limbo Down\nDROP TABLE notes;\n", encoding="utf-8")
            loaded = load_directory(directory)
            self.assertEqual(loaded[0].version, "001")
        database = Database(":memory:")
        migrator = Migrator(database)
        with self.assertRaises(MigrationError):
            migrator.apply([Migration("003", "bad", "CREATE TABLE ok (id INTEGER); INSERT INTO missing VALUES (1);")])
        self.assertEqual(migrator.applied(), {})

    def test_external_databases_are_rejected(self):
        with self.assertRaisesRegex(DatabaseURLError, "postgres"):
            open_database("postgres://localhost/app")
        with self.assertRaisesRegex(DatabaseURLError, "mysql"):
            open_database("mysql://localhost/app")
        database = open_database("sqlite:///:memory:")
        self.assertIsInstance(database, Database)
        database.close()

    def test_duplicate_version(self):
        migrator = Migrator(Database(":memory:"))
        with self.assertRaisesRegex(MigrationError, "Duplicate"):
            migrator.apply([Migration("001", "a", "SELECT 1"), Migration("001", "b", "SELECT 2")])


if __name__ == "__main__":
    unittest.main()
