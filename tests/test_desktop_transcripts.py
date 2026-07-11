import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HOOK_PATH = Path(__file__).parents[1] / "hooks" / "langfuse_hook.py"
SPEC = importlib.util.spec_from_file_location("langfuse_hook", HOOK_PATH)
assert SPEC and SPEC.loader
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


class DesktopTranscriptTests(unittest.TestCase):
    def test_recovers_desktop_home_scoped_path_using_cwd(self):
        with tempfile.TemporaryDirectory() as home:
            projects = Path(home) / ".claude" / "projects"
            cwd = "/Users/example/work/project"
            session_id = "session-123"
            actual = projects / "-Users-example-work-project" / f"{session_id}.jsonl"
            actual.parent.mkdir(parents=True)
            actual.touch()
            broken = projects / "-Users-example" / f"{session_id}.jsonl"

            with patch.object(HOOK.Path, "home", return_value=Path(home)):
                resolved = HOOK.resolve_transcript_path(
                    session_id, broken, {"cwd": cwd}
                )

            self.assertEqual(resolved, actual.resolve())

    def test_keeps_broken_path_when_multiple_candidates_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as home:
            projects = Path(home) / ".claude" / "projects"
            session_id = "session-123"
            for directory in ("project-a", "project-b"):
                candidate = projects / directory / f"{session_id}.jsonl"
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.touch()
            broken = projects / "missing" / f"{session_id}.jsonl"

            with patch.object(HOOK.Path, "home", return_value=Path(home)):
                resolved = HOOK.resolve_transcript_path(session_id, broken, {})

            self.assertEqual(resolved, broken)

    def test_discovers_nested_desktop_workflow_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "parent.jsonl"
            transcript.touch()
            root = Path(directory) / "parent" / "subagents"
            direct = root / "agent-direct.jsonl"
            nested = root / "workflows" / "wf-1" / "agent-nested.jsonl"
            journal = root / "workflows" / "wf-1" / "journal.jsonl"
            for path in (direct, nested, journal):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            discovered = HOOK.subagent_transcripts(transcript, "parent")

            self.assertEqual(
                discovered,
                [
                    ("parent:agent-direct", direct.resolve()),
                    ("parent:agent-nested", nested.resolve()),
                ],
            )


if __name__ == "__main__":
    unittest.main()
