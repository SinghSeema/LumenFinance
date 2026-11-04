import json
from pathlib import Path

from emi_agent.state import Borrower

_PATH = Path(__file__).parent.parent.parent / "data" / "borrowers.json"


def load_borrowers(path: Path = _PATH) -> dict[str, Borrower]:
    records = [Borrower.model_validate(r) for r in json.loads(path.read_text())]
    return {b.borrower_id: b for b in records}
