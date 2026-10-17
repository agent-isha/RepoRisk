import unittest

from reporisk.filters import is_doc_file, is_noise, is_relevant, is_test_file
from reporisk.mining import FileStats, rank_files


class NoiseFilterTests(unittest.TestCase):
    def test_excludes_noise_paths(self):
        excluded = [
            # lock files, at the root and nested
            "Pipfile.lock",
            "poetry.lock",
            "package-lock.json",
            "frontend/yarn.lock",
            "web/pnpm-lock.yaml",
            # certificates, keys and logs
            "requests/cacert.pem",
            "deploy/server.key",
            "certs/ca.CRT",
            "logs/app.log",
            # cache and generated files
            "pkg/__pycache__/models.cpython-39.pyc",
            "pkg/__pycache__/notes.txt",
            "build_helpers/thing.pyc",
            "static/js/app.min.js",
            "static/js/app.js.map",
            "ext/requests-logo.ai",
            # vendored dependencies
            "vendor/github.com/pkg/errors/errors.go",
            "src/pip/_vendor/urllib3/poolmanager.py",
            "web/node_modules/react/index.js",
            "requests/packages/idna/idnadata.py",
            "requests/packages/urllib3/connectionpool.py",
        ]
        for path in excluded:
            with self.subTest(path=path):
                self.assertTrue(is_noise(path))

    def test_keeps_source_files(self):
        kept = [
            "requests/models.py",
            "src/index.js",
            "src/app/main.ts",
            "src/main/java/com/example/App.java",
            "engine/render.cpp",
            "include/render.hpp",
            "cmd/server/main.go",
            "README.md",
            # top-level packages/ is first-party monorepo code
            "packages/core/src/index.ts",
            # names that only resemble excluded entries
            "src/vendors.py",
            "src/keyboard.py",
            "src/lockfile.py",
            "src/distance.py",
        ]
        for path in kept:
            with self.subTest(path=path):
                self.assertFalse(is_noise(path))

    def test_windows_separators(self):
        self.assertTrue(is_noise(r"requests\packages\idna\idnadata.py"))
        self.assertFalse(is_noise(r"requests\models.py"))

    def test_extra_patterns(self):
        self.assertFalse(is_noise("docs/user/advanced.rst"))
        self.assertTrue(is_noise("docs/user/advanced.rst", extra_patterns=["docs/*"]))
        self.assertTrue(is_noise("HISTORY.rst", extra_patterns=["*.rst"]))


class TestFileDetectionTests(unittest.TestCase):
    def test_detects_test_files(self):
        for path in [
            "tests/test_requests.py",
            "test_requests.py",
            "pkg/utils_test.py",
            "server/handler_test.go",
            "src/App.test.tsx",
            "src/api.spec.ts",
            "src/test/java/com/example/AppTest.java",
            "src/__tests__/button.js",
        ]:
            with self.subTest(path=path):
                self.assertTrue(is_test_file(path))

    def test_ignores_non_test_files(self):
        for path in ["requests/models.py", "src/contest.py", "src/latest.js", "testing.md"]:
            with self.subTest(path=path):
                self.assertFalse(is_test_file(path))

    def test_tests_included_by_default(self):
        self.assertTrue(is_relevant("tests/test_requests.py"))
        self.assertFalse(is_relevant("tests/test_requests.py", include_tests=False))
        self.assertTrue(is_relevant("requests/models.py", include_tests=False))


class DocFileTests(unittest.TestCase):
    def test_detects_doc_files(self):
        for path in [
            "README.md",
            "README.rst",
            "readme.txt",
            "README",
            "README.zh-CN.md",
            "HISTORY.rst",
            "HISTORY.md",
            "CHANGELOG.md",
            "CHANGES.txt",
            "NEWS",
            "CONTRIBUTING.md",
            "packages/core/README.md",
            "docs/user/advanced.rst",
            "docs/conf.py",
            "doc/index.md",
            "documentation/guide/setup.adoc",
            r"docs\user\quickstart.rst",
        ]:
            with self.subTest(path=path):
                self.assertTrue(is_doc_file(path))

    def test_keeps_source_files_with_doc_like_names(self):
        for path in [
            "requests/models.py",
            "src/changes.py",
            "src/history.js",
            "lib/readme_parser.py",
            "src/news/feed.ts",
            "src/docstring.py",
            "src/main/java/com/example/Docs.java",
        ]:
            with self.subTest(path=path):
                self.assertFalse(is_doc_file(path))

    def test_docs_excluded_by_default_and_configurable(self):
        self.assertFalse(is_relevant("HISTORY.rst"))
        self.assertFalse(is_relevant("docs/user/advanced.rst"))
        self.assertTrue(is_relevant("HISTORY.rst", include_docs=True))
        self.assertTrue(is_relevant("docs/user/advanced.rst", include_docs=True))

    def test_include_docs_does_not_bring_back_noise(self):
        self.assertFalse(is_relevant("docs/_static/logo.png", include_docs=True))


class RankFilesTests(unittest.TestCase):
    def setUp(self):
        self.files = {
            "requests/cacert.pem": FileStats(commits=10, added=20000, deleted=3000),
            "requests/models.py": FileStats(commits=700, added=7000, deleted=4000),
            "HISTORY.rst": FileStats(commits=320, added=3000, deleted=650),
            "tests/test_requests.py": FileStats(commits=250, added=5000, deleted=2700),
            "requests/sessions.py": FileStats(commits=330, added=2500, deleted=1700),
        }

    def test_ranks_relevant_files_by_churn(self):
        ranked = [path for path, _ in rank_files(self.files)]
        self.assertEqual(
            ranked, ["requests/models.py", "tests/test_requests.py", "requests/sessions.py"]
        )

    def test_exclude_tests_option(self):
        ranked = [path for path, _ in rank_files(self.files, include_tests=False)]
        self.assertEqual(ranked, ["requests/models.py", "requests/sessions.py"])

    def test_include_docs_option(self):
        ranked = [path for path, _ in rank_files(self.files, include_docs=True)]
        self.assertEqual(
            ranked,
            ["requests/models.py", "tests/test_requests.py", "requests/sessions.py", "HISTORY.rst"],
        )

    def test_raw_stats_are_untouched(self):
        rank_files(self.files, include_tests=False)
        self.assertEqual(len(self.files), 5)
        self.assertIn("requests/cacert.pem", self.files)
        self.assertIn("HISTORY.rst", self.files)


if __name__ == "__main__":
    unittest.main()
