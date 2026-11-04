"""Decision 78: an uploaded file's path is only kept when it is safe to save."""

import pytest

from app.services.uploads import clean_path


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        ("October 2026/21/bill.pdf", "October 2026/21/bill.pdf"),
        ("October 2026\\21\\bill.pdf", "October 2026/21/bill.pdf"),  # sent from Windows
        ("./a//b.pdf", "a/b.pdf"),
        ("bill.pdf", "bill.pdf"),
    ],
)
def test_a_safe_path_is_kept_as_sent(sent: str, kept: str) -> None:
    assert str(clean_path(sent)) == kept


@pytest.mark.parametrize(
    "sent",
    [
        "../bill.pdf",
        "a/../../bill.pdf",
        "/etc/bill.pdf",
        "\\server\\bill.pdf",
        "",
        "/",
        "October/.hidden/bill.pdf",
        "October/._bill.pdf",  # macOS copies these beside every file
        "October/~$bill.pdf",  # Office temporary file
        "bill\x00.pdf",
    ],
)
def test_an_unsafe_or_hidden_path_is_refused(sent: str) -> None:
    assert clean_path(sent) is None
