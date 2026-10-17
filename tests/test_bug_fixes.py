import unittest
from datetime import datetime, timedelta, timezone

from reporisk.bug_fixes import (
    BugFixRules,
    Outcome,
    classify,
    classify_commit,
    detect_bug_fixes,
)
from reporisk.mining import CommitRecord, FileChange

LIKELY, UNCERTAIN, NOT_LIKELY = Outcome.LIKELY, Outcome.UNCERTAIN, Outcome.NOT_LIKELY
CODE = ["requests/models.py"]
DATE = datetime(2024, 6, 1, tzinfo=timezone.utc)


def outcome(message, paths=CODE, rules=BugFixRules()):
    return classify(message, paths, rules)[0]


class PositiveCaseTests(unittest.TestCase):
    def test_strong_terms(self):
        for message in [
            "Fix crash when response body is empty",
            "Fix regression in redirect handling",
            "Bugfix: cookies lost on redirect",
            "Hotfix for proxy authentication",
            "Handle segfault in C extension",
            "Fix #4321",
            "fixes #12: wrong encoding on 304",
            "Correct defect in chunked upload",
            "Bug in Session.merge_environment_settings",
        ]:
            with self.subTest(message=message):
                self.assertEqual(outcome(message), LIKELY)

    def test_two_weak_terms(self):
        self.assertEqual(outcome("Fix incorrect Content-Length header"), LIKELY)
        self.assertEqual(outcome("Resolve error raised for unicode URLs"), LIKELY)

    def test_issue_reference_in_body(self):
        message = "Handle empty Location header\n\nFixes #2803"
        result, evidence = classify(message, CODE)
        self.assertEqual(result, LIKELY)
        self.assertIn("strong:fixes #2803", evidence)

    def test_closes_reference_is_weak(self):
        result, evidence = classify("Add default headers setting\n\nCloses #78", CODE)
        self.assertEqual(result, UNCERTAIN)
        self.assertEqual(evidence, ("weak:closes #78",))

    def test_single_weak_term_is_uncertain(self):
        self.assertEqual(outcome("Fix proxy environment handling"), UNCERTAIN)
        self.assertEqual(outcome("Raise error on invalid schema"), UNCERTAIN)


class FalsePositiveTests(unittest.TestCase):
    def test_typo_and_formatting(self):
        for message in [
            "Fix typo in models.py",
            "fix spelling",
            "Fix whitespace",
            "Fix formatting with black",
            "Fix flake8 warnings",
            "Fix PEP8 issues",
            "fix lint",
        ]:
            with self.subTest(message=message):
                self.assertEqual(outcome(message), NOT_LIKELY)

    def test_documentation_fixes(self):
        self.assertEqual(outcome("Fix docs"), NOT_LIKELY)
        self.assertEqual(outcome("Fix broken links in README"), NOT_LIKELY)
        # docs-only commits are not bug fixes even with strong wording
        self.assertEqual(outcome("Fix crash example", ["docs/user/quickstart.rst"]), NOT_LIKELY)

    def test_dependency_updates(self):
        self.assertEqual(outcome("Bump urllib3 to fix security issue"), NOT_LIKELY)
        self.assertEqual(outcome("Upgrade dependencies"), NOT_LIKELY)
        self.assertEqual(outcome("Fix build", ["requirements-dev.txt"]), NOT_LIKELY)
        self.assertEqual(outcome("Fix lock file", ["Pipfile.lock"]), NOT_LIKELY)

    def test_word_boundaries(self):
        for message in [
            "Add prefix option to adapters",
            "Add pytest fixture for proxies",
            "Add debug logging",
            "Improve suffix handling",
            "Use dispatch table",
        ]:
            with self.subTest(message=message):
                self.assertEqual(outcome(message), NOT_LIKELY)

    def test_ci_and_cosmetic_fixes(self):
        self.assertEqual(outcome("Fix CI and build failures"), NOT_LIKELY)
        self.assertEqual(outcome("ci: fix flaky job"), NOT_LIKELY)
        self.assertEqual(outcome("Fix missing space in error message"), NOT_LIKELY)
        result, evidence = classify(
            "Fix syntax error in lock action", [".github/workflows/lock-issues.yml"]
        )
        self.assertEqual(result, NOT_LIKELY)
        self.assertIn("files:ci-only", evidence)
        # CI files alongside code are not CI-only
        paths = [".github/workflows/run-tests.yml", "requests/models.py"]
        self.assertEqual(outcome("Fix crash on redirect", paths), LIKELY)

    def test_revert_and_merge_conflict(self):
        self.assertEqual(outcome('Revert "Fix proxy handling"'), NOT_LIKELY)
        self.assertEqual(outcome("Resolve merge conflicts"), NOT_LIKELY)

    def test_body_release_notes_are_ignored(self):
        message = "Bump certifi from 2023.5.7 to 2023.7.22\n\nRelease notes: bug fixes and a crash fix"
        self.assertEqual(outcome(message, ["requirements-dev.txt"]), NOT_LIKELY)
        self.assertEqual(outcome("Add feature flag\n\nThis also fixes a bug."), NOT_LIKELY)

    def test_tests_only_changes_are_capped(self):
        paths = ["tests/test_requests.py"]
        self.assertEqual(outcome("Fix crash in flaky test", paths), UNCERTAIN)


class MixedMessageTests(unittest.TestCase):
    def test_strong_and_non_bug(self):
        result, evidence = classify("Fix typo and crash on empty input", CODE)
        self.assertEqual(result, UNCERTAIN)
        self.assertIn("strong:crash", evidence)
        self.assertIn("non-bug:typo", evidence)

    def test_weak_and_non_bug(self):
        self.assertEqual(outcome("Fix docstring and error handling"), NOT_LIKELY)

    def test_feature_with_fix_word(self):
        self.assertEqual(outcome("Add retries and fix timeout"), UNCERTAIN)


class EmptyAndCaseTests(unittest.TestCase):
    def test_empty_messages(self):
        for message in ["", "   ", "\n\n", None]:
            with self.subTest(message=message):
                result, evidence = classify(message, CODE)
                self.assertEqual(result, NOT_LIKELY)
                self.assertEqual(evidence, ("no bug-fix keywords",))

    def test_no_changed_files(self):
        self.assertEqual(outcome("Fix crash", []), LIKELY)

    def test_case_insensitive(self):
        for message in ["FIX CRASH", "fix crash", "Fix Crash", "fIx cRaSh"]:
            with self.subTest(message=message):
                self.assertEqual(outcome(message), LIKELY)
        self.assertEqual(outcome("FIX TYPO"), NOT_LIKELY)

    def test_evidence_is_lowercase(self):
        _, evidence = classify("REGRESSION in Session", CODE)
        self.assertEqual(evidence, ("strong:regression",))


class ConfigurableRulesTests(unittest.TestCase):
    def test_custom_terms(self):
        rules = BugFixRules(strong_terms=("oops",))
        self.assertEqual(outcome("Oops, wrong header", rules=rules), LIKELY)
        self.assertEqual(outcome("Fix crash", rules=rules), UNCERTAIN)  # crash no longer strong

    def test_weak_threshold(self):
        rules = BugFixRules(min_weak_terms_for_likely=1)
        self.assertEqual(outcome("Fix proxy environment handling", rules=rules), LIKELY)


class CommitLevelTests(unittest.TestCase):
    def test_multi_file_commit(self):
        commit = CommitRecord(
            "abc123",
            "dev@x.com",
            DATE,
            (
                FileChange("requests/models.py", 10, 4),
                FileChange("requests/sessions.py", 3, 1),
                FileChange("tests/test_requests.py", 20, 0),
            ),
            "Fix regression in redirect handling\n\nCloses #99",
        )
        candidate = classify_commit(commit)
        self.assertEqual(candidate.outcome, LIKELY)
        self.assertEqual(candidate.hash, "abc123")
        self.assertEqual(candidate.timestamp, DATE)
        self.assertEqual(candidate.subject, "Fix regression in redirect handling")
        self.assertEqual(
            candidate.paths,
            ("requests/models.py", "requests/sessions.py", "tests/test_requests.py"),
        )
        self.assertEqual((candidate.added, candidate.deleted), (33, 5))
        self.assertIn("strong:regression", candidate.evidence)
        self.assertIn("weak:closes #99", candidate.evidence)

    def test_mixed_code_and_docs_is_not_docs_only(self):
        paths = ["requests/models.py", "docs/user/advanced.rst"]
        self.assertEqual(outcome("Fix crash on redirect", paths), LIKELY)

    def test_detect_respects_cutoff(self):
        commits = [
            CommitRecord("old", "a", DATE, (FileChange("a.py", 1, 0),), "Fix crash"),
            CommitRecord("new", "a", DATE + timedelta(days=1), (FileChange("a.py", 1, 0),), "Fix bug"),
        ]
        self.assertEqual([c.hash for c in detect_bug_fixes(commits)], ["old", "new"])
        self.assertEqual([c.hash for c in detect_bug_fixes(commits, cutoff=DATE)], ["old"])


if __name__ == "__main__":
    unittest.main()
