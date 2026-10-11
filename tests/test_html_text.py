from jazz_agent.core.html_text import html_to_text


def test_paragraphs_become_lines_and_entities_decode() -> None:
    markup = "<p>Guitarist Steve Ragsdale &amp; friends.</p>\n<p>Standards <b>and</b> more.</p>"

    assert html_to_text(markup) == "Guitarist Steve Ragsdale & friends.\nStandards and more."


def test_scripts_and_styles_are_dropped() -> None:
    assert html_to_text("<style>p{}</style><p>Hi</p><script>x()</script>") == "Hi"


def test_empty_markup() -> None:
    assert html_to_text("") == ""
