"""PJ-generated skill: markdown_table_to_csv (SkillOps)."""
SCHEMA = {
  "type": "function",
  "name": "markdown_table_to_csv",
  "description": "Convert a markdown table into CSV text.",
  "parameters": {
    "type": "object",
    "properties": {
      "markdown": {
        "type": "string"
      }
    },
    "required": [
      "markdown"
    ]
  }
}

import csv, io
def run(markdown: str) -> dict:
    rows = [ [c.strip() for c in line.strip().strip("|").split("|")]
             for line in markdown.strip().splitlines()
             if line.strip().startswith("|") and not set(line) <= set("|-: ") ]
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return {"csv": buf.getvalue(), "rows": len(rows)}

