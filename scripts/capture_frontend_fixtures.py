"""Capture real API responses as frontend test fixtures.

Builds the synthetic repository from tests/synthetic_repo.py in a temporary
directory, runs a real analysis through the FastAPI app, and writes each
response to frontend/src/test/fixtures/. The temporary repository path is
replaced with a placeholder so no local path ends up in the repo.

Run from the project root:  python scripts/capture_frontend_fixtures.py
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from reporisk.api.app import create_app  # noqa: E402
from reporisk.api.config import Settings, build_repositories  # noqa: E402
from tests import synthetic_repo  # noqa: E402

OUT = ROOT / "frontend" / "src" / "test" / "fixtures"
OPTIONS = {"horizon_days": 30, "snapshot_interval_days": 30}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        repo = synthetic_repo.build(str(Path(tmp) / "demo-repo"))
        settings = Settings(repositories=build_repositories([repo]), data_dir=Path(tmp) / "data")
        app = create_app(settings)
        client = TestClient(app)

        def run(options):
            job = client.post("/api/analyze", json={"repository_id": "demo-repo", "options": options}).json()
            app.state.jobs.wait(job["id"], timeout=300)
            return job["id"]

        full = run(OPTIONS)
        no_model = run({**OPTIONS, "train_model": False})
        responses = {
            "health.json": client.get("/health"),
            "repositories.json": client.get("/api/repositories"),
            "analysis-status.json": client.get(f"/api/analysis/{full}"),
            "summary.json": client.get(f"/api/analysis/{full}/summary"),
            "files.json": client.get(f"/api/analysis/{full}/files", params={"limit": 1000}),
            "predictions.json": client.get(f"/api/analysis/{full}/predictions"),
            "predictions-unavailable.json": client.get(f"/api/analysis/{no_model}/predictions"),
            "bug-fixes.json": client.get(f"/api/analysis/{full}/bug-fixes", params={"limit": 8}),
        }
        for name, response in responses.items():
            response.raise_for_status()
            text = json.dumps(response.json(), indent=2)
            for local in {repo, str(Path(repo).resolve())}:  # macOS: /var -> /private/var
                text = text.replace(local, "/path/to/demo-repo")
            (OUT / name).write_text(text + "\n")
            print(f"wrote {OUT.relative_to(ROOT) / name}")


if __name__ == "__main__":
    main()
