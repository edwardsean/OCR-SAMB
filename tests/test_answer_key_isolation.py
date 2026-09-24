"""The answer key (testdata/golden_p1-32.json) is for grading only. Pipeline code must never read it,
or the tests would be grading the pipeline against itself."""
import pathlib
import re

PIPELINE = ["common", "worker", "grouper", "publisher"]
FORBIDDEN = re.compile(r"golden|testdata|answer.?key", re.I)


def test_pipeline_never_references_the_answer_key():
    root = pathlib.Path("/app")
    hits = []
    for pkg in PIPELINE:
        for f in (root / pkg).rglob("*"):
            if f.suffix in (".py", ".json", ".sql") and f.is_file():
                for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                    if FORBIDDEN.search(line):
                        hits.append(f"{f.relative_to(root)}:{i}: {line.strip()[:100]}")
    assert not hits, hits
