from yaml import safe_load
from pathlib import Path
import json

dfile = Path(__file__).parent / "data.yaml"
data = safe_load(dfile.read_text(encoding="utf-8"))

conection = data.pop("connection", None)
if not conection:
    exit("No connections found in data.yaml")

print(json.dumps(data, indent=2, ensure_ascii=False))