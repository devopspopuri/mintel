from app.web.router import _rich_text_plain_text, _sanitize_rich_text


def test_sanitize_rich_text_allows_basic_formatting_and_links():
    html = '<p>Hello <strong>team</strong> <a href="https://example.com" onclick="bad()">link</a></p>'

    assert _sanitize_rich_text(html) == (
        '<p>Hello <strong>team</strong> '
        '<a href="https://example.com" target="_blank" rel="noopener noreferrer">link</a></p>'
    )


def test_sanitize_rich_text_removes_scripts_and_bad_urls():
    html = '<p>Safe</p><script>alert(1)</script><a href="javascript:alert(1)">bad</a>'

    assert _sanitize_rich_text(html) == "<p>Safe</p>bad"


def test_rich_text_plain_text_counts_words_without_tags():
    html = "<p>One two <strong>three</strong></p><ul><li>four five</li></ul>"

    assert _rich_text_plain_text(html) == "One two three four five"
