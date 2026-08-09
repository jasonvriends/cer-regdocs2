from regdocs_atlas.runtime.presentation import format_progress


def test_progress_zero_padding_tracks_total_width():
    assert format_progress(7, 100) == "007/100"
    assert format_progress(7, 4071) == "0007/4071"
    assert format_progress(7, 12000) == "00007/12000"
    assert format_progress(0, 0) == "000/000"
