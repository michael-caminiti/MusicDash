import markdown


def render_review_html(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read()
    return markdown.markdown(raw, extensions=["extra", "sane_lists"])
