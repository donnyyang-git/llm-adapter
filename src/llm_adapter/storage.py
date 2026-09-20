import os
import re
import tempfile
from pathlib import Path

from llm_adapter.models import ConversationSession


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class ConversationStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, session: ConversationSession) -> tuple[Path, Path]:
        self.directory.mkdir(parents=True, exist_ok=True)
        json_path = self._path_for(session.session_id, ".json")
        markdown_path = self._path_for(session.session_id, ".md")
        self._atomic_write(json_path, session.model_dump_json(indent=2) + "\n")
        self._atomic_write(markdown_path, self.render_markdown(session))
        return json_path, markdown_path

    def load(self, session_id: str) -> ConversationSession:
        return ConversationSession.model_validate_json(
            self._path_for(session_id, ".json").read_text(encoding="utf-8")
        )

    def list_sessions(self) -> list[ConversationSession]:
        if not self.directory.exists():
            return []
        sessions = [
            ConversationSession.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.directory.glob("*.json")
        ]
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    @staticmethod
    def render_markdown(session: ConversationSession) -> str:
        lines = [
            f"# {session.title}",
            "",
            f"- Session: `{session.session_id}`",
            f"- Conversation: {session.conversation_url}",
            f"- Updated: {session.updated_at.isoformat()}",
            "",
        ]
        for index, turn in enumerate(session.turns, start=1):
            lines.extend(
                [
                    f"## Turn {index}",
                    "",
                    "### User",
                    "",
                    turn.question,
                    "",
                    "### Gemini",
                    "",
                    turn.response_markdown or turn.response_text,
                    "",
                    f"Sent: {turn.sent_at.isoformat()}",
                    f"Completed: {turn.completed_at.isoformat() if turn.completed_at else 'N/A'}",
                    "",
                    f"Status: `{turn.status}`",
                ]
            )
            if turn.error:
                lines.extend(["", f"Error: {turn.error}"])
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _path_for(self, session_id: str, suffix: str) -> Path:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("Invalid session ID.")
        return self.directory / f"{session_id}{suffix}"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)