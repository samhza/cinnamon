import tempfile
import os


def reserve(suffix: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        try:
            f.close()
            return f.name
        except Exception as e:
            os.remove(f.name)
            raise e
