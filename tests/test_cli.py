import pytest

from app import cli


def test_start_applies_service_validation(tmp_path):
    # A renamed non-PDF is rejected by the service's magic-byte check, as it is in the API
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"MZ\x90 not a pdf")
    jd = tmp_path / "jd.txt"
    jd.write_text("Senior Python engineer. " * 5)

    with pytest.raises(SystemExit, match="Resume must be a PDF"):
        cli.main(["start", str(resume), str(jd)])


def test_missing_file_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["start", str(tmp_path / "nope.pdf"), str(tmp_path / "jd.txt")])
    assert exc.value.code == 2  # argparse usage error
    assert "nope.pdf" in capsys.readouterr().err


def test_review_unknown_thread_exits_with_message():
    with pytest.raises(SystemExit, match="not found"):
        cli.main(["review", "missing"])
