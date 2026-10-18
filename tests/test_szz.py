import json
import os
import shutil
import subprocess
import tempfile
import unittest

from reporisk.szz import SZZConfig, trace_fix, trace_repo, write_jsonl


class TempRepo:
    """A throwaway git repository with scripted, dated commits."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = self._tmp.name
        self._day = 0
        self.git("init", "-q", "-b", "main")

    def cleanup(self):
        self._tmp.cleanup()

    def git(self, *args, email="dev@x.com"):
        date = f"2024-01-{self._day + 1:02d}T10:00:00+00:00"
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": email,
            "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
        }
        out = subprocess.run(
            ["git", *args], cwd=self.path, env=env, check=True, capture_output=True, text=True
        )
        return out.stdout.strip()

    def write(self, name, content):
        full = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(full, mode) as f:
            f.write(content)

    def commit(self, message, files=None, remove=(), email="dev@x.com"):
        for name, content in (files or {}).items():
            self.write(name, content)
        for name in remove:
            self.git("rm", "-q", name)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, email=email)
        self._day += 1
        return self.git("rev-parse", "HEAD")


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class SZZTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()

    def tearDown(self):
        self.repo.cleanup()


class TraceFaultyLineTests(SZZTestCase):
    def test_fix_traces_back_to_faulty_commit(self):
        r = self.repo
        r.commit("Add calculator", {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"})
        faulty = r.commit("Refactor sub", {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return b - a\n"})
        r.commit("Add mul", {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return b - a\n\n\ndef mul(a, b):\n    return a * b\n"})
        fix = r.commit("Fix incorrect sub result", {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"})

        result = trace_fix(r.path, fix)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.fix_hash, fix)
        self.assertEqual(record.inducing_hash, faulty)
        self.assertEqual(record.path, "calc.py")
        self.assertEqual(len(record.lines), 1)
        line = record.lines[0]
        self.assertEqual(line.content, "    return b - a")
        self.assertEqual((line.fix_parent_line, line.inducing_line), (6, 6))
        self.assertTrue(any("blame" in e for e in record.evidence))
        self.assertTrue(any("last commit to change the line" in l for l in record.limitations))
        self.assertIsNotNone(record.inducing_date)

    def test_lines_from_two_commits_give_two_records(self):
        r = self.repo
        first = r.commit("Add", {"app.py": "x = 1\ny = 2\nz = 3\n"})
        second = r.commit("Change y", {"app.py": "x = 1\ny = 20\nz = 3\n"})
        fix = r.commit("Fix wrong values", {"app.py": "x = 10\ny = 2\nz = 3\n"})

        records = trace_fix(r.path, fix).records
        by_hash = {rec.inducing_hash: rec for rec in records}
        self.assertEqual(set(by_hash), {first, second})
        self.assertEqual(by_hash[first].lines[0].content, "x = 1")
        self.assertEqual(by_hash[second].lines[0].content, "y = 20")

    def test_whitespace_only_change_is_ignored_by_blame(self):
        r = self.repo
        faulty = r.commit("Add", {"app.py": "def f():\n    return 1 / 0\n"})
        r.commit("Reindent", {"app.py": "def f():\n        return 1 / 0\n"})
        fix = r.commit("Fix crash", {"app.py": "def f():\n    return 1\n"})
        self.assertEqual(trace_fix(r.path, fix).inducing_hashes, {faulty})

        reindent_blamed = trace_fix(r.path, fix, SZZConfig(ignore_whitespace=False))
        self.assertNotEqual(reindent_blamed.inducing_hashes, {faulty})

    def test_blank_and_comment_lines_are_not_traced(self):
        r = self.repo
        r.commit("Add", {"app.py": "# old comment\n\nvalue = 1\n"})
        fix = r.commit("Fix bug", {"app.py": "value = 1\n"})
        result = trace_fix(r.path, fix)
        self.assertEqual(result.records, [])
        self.assertIsNone(result.error)

    def test_syntax_only_lines_are_not_traced(self):
        r = self.repo
        r.commit("Add", {"app.py": "try:\n    run(\n        1,\n    )\nexcept ValueError:\n    pass\n"})
        layout = r.commit("Reformat", {"app.py": "try:\n    run(\n        1,\n    )\nexcept ValueError:\n    pass\n\n"})
        fix = r.commit("Fix crash", {"app.py": "run(2)\n"})

        traced = trace_fix(r.path, fix)
        contents = {line.content.strip() for rec in traced.records for line in rec.lines}
        self.assertEqual(contents, {"run(", "1,", "except ValueError:"})

        everything = trace_fix(r.path, fix, SZZConfig(skip_trivial_lines=False))
        contents = {line.content.strip() for rec in everything.records for line in rec.lines}
        self.assertEqual(contents, {"try:", "run(", "1,", ")", "except ValueError:", "pass"})
        self.assertNotIn(layout, everything.inducing_hashes)

    def test_additions_only_fix_has_no_records(self):
        r = self.repo
        r.commit("Add", {"app.py": "value = 1\n"})
        fix = r.commit("Fix crash on import", {"app.py": "value = 1\nother = 2\n"})
        result = trace_fix(r.path, fix)
        self.assertEqual(result.records, [])
        self.assertEqual(result.skipped, [])


class FileKindTests(SZZTestCase):
    def test_test_and_doc_files_skipped_by_default(self):
        r = self.repo
        r.commit("Add", {"tests/test_app.py": "assert 1 == 2\n", "docs/guide.md": "Use x\n"})
        fix = r.commit("Fix bug", {"tests/test_app.py": "assert 1 == 1\n", "docs/guide.md": "Use y\n"})

        result = trace_fix(r.path, fix)
        self.assertEqual(result.records, [])
        self.assertEqual(
            {(s.path, s.reason) for s in result.skipped},
            {("tests/test_app.py", "test file"), ("docs/guide.md", "documentation file")},
        )

    def test_test_files_traced_when_enabled(self):
        r = self.repo
        faulty = r.commit("Add", {"tests/test_app.py": "assert 1 == 2\n"})
        fix = r.commit("Fix bug", {"tests/test_app.py": "assert 1 == 1\n"})
        records = trace_fix(r.path, fix, SZZConfig(trace_test_files=True)).records
        self.assertEqual([rec.inducing_hash for rec in records], [faulty])
        self.assertIn("test file", records[0].evidence[0])

    def test_noise_files_skipped(self):
        r = self.repo
        r.commit("Add", {"package-lock.json": '{"a": 1}\n'})
        fix = r.commit("Fix bug", {"package-lock.json": '{"a": 2}\n'})
        self.assertEqual(trace_fix(r.path, fix).skipped[0].path, "package-lock.json")


class UnsupportedCaseTests(SZZTestCase):
    def test_deleted_file(self):
        r = self.repo
        r.commit("Add", {"old.py": "broken = True\n", "app.py": "x = 1\n"})
        fix = r.commit("Fix crash by removing module", remove=["old.py"])
        result = trace_fix(r.path, fix)
        self.assertEqual(result.records, [])
        self.assertEqual([(s.path, s.reason) for s in result.skipped],
                         [("old.py", "file deleted by the fix")])

    def test_renamed_file_traces_old_path(self):
        r = self.repo
        faulty = r.commit("Add", {"old_name.py": "a = 1\nb = 2\nc = 3\nd = 4\n"})
        r.git("mv", "old_name.py", "new_name.py")
        fix = r.commit("Fix bug and rename", {"new_name.py": "a = 1\nb = 3\nc = 3\nd = 4\n"})

        records = trace_fix(r.path, fix).records
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].inducing_hash, records[0].path), (faulty, "old_name.py"))
        self.assertTrue(any("renamed" in l for l in records[0].limitations))

    def test_binary_file(self):
        r = self.repo
        r.commit("Add", {"data.bin": bytes(range(256))})
        fix = r.commit("Fix corrupt data", {"data.bin": bytes(reversed(range(256)))})
        result = trace_fix(r.path, fix)
        self.assertEqual(result.records, [])
        self.assertEqual([(s.path, s.reason) for s in result.skipped], [("data.bin", "binary file")])

    def test_root_commit(self):
        fix = self.repo.commit("Fix bug", {"app.py": "x = 1\n"})
        self.assertEqual(trace_fix(self.repo.path, fix).error, "root commit has no parent to blame")

    def test_merge_commit(self):
        r = self.repo
        r.commit("Add", {"app.py": "x = 1\n"})
        r.git("checkout", "-q", "-b", "feature")
        r.commit("Fix bug on branch", {"app.py": "x = 2\n"})
        r.git("checkout", "-q", "main")
        r.commit("Other", {"other.py": "y = 1\n"})
        r.git("merge", "-q", "--no-ff", "feature", "-m", "Merge fix")
        merge = r.git("rev-parse", "HEAD")
        self.assertEqual(trace_fix(r.path, merge).error, "merge commit (not traced)")

    def test_unknown_commit(self):
        self.repo.commit("Add", {"app.py": "x = 1\n"})
        result = trace_fix(self.repo.path, "0" * 40)
        self.assertTrue(result.error.startswith("cannot read commit"))

    def test_large_rewrite_skipped(self):
        r = self.repo
        r.commit("Add", {"app.py": "".join(f"v{i} = {i}\n" for i in range(20))})
        fix = r.commit("Fix bug", {"app.py": "".join(f"v{i} = {i + 1}\n" for i in range(20))})
        result = trace_fix(r.path, fix, SZZConfig(max_deleted_lines_per_file=5))
        self.assertEqual(result.records, [])
        self.assertIn("exceed the limit", result.skipped[0].reason)


class TraceRepoTests(SZZTestCase):
    def test_only_likely_bug_fixes_are_traced(self):
        r = self.repo
        faulty = r.commit("Add parser", {"parser.py": "def parse(s):\n    return s.split(',')[1]\n"})
        r.commit("Fix typo in parser", {"parser.py": "def parse(s):\n    return s.split(',')[1]  # first\n"})
        fix = r.commit("Fix crash on empty input", {"parser.py": "def parse(s):\n    return s.split(',')[0]\n"})

        results = trace_repo(r.path)
        self.assertEqual([res.fix_hash for res in results], [fix])
        self.assertEqual(len(results[0].inducing_hashes), 1)
        # the typo commit last touched the line, so blame points at it, not "Add parser"
        self.assertNotEqual(results[0].inducing_hashes, {faulty})

    def test_write_jsonl(self):
        r = self.repo
        faulty = r.commit("Add", {"app.py": "x = 1\n"})
        r.commit("Fix crash", {"app.py": "x = 2\n"})
        out = os.path.join(r.path, "out.jsonl")
        self.assertEqual(write_jsonl(trace_repo(r.path), out), 1)
        with open(out) as f:
            row = json.loads(f.readline())
        self.assertEqual(row["inducing_hash"], faulty)
        self.assertEqual(row["path"], "app.py")
        self.assertEqual(row["lines"][0]["content"], "x = 1")
        self.assertIn("limitations", row)


if __name__ == "__main__":
    unittest.main()
