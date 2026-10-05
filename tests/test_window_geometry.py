"""Tests for fit_rect_to_work_area, the pure fitting math behind
_restore_geometry. The Win32 calls around it need a live desktop window and
are covered by the manual smoke test instead.
"""

from sfl import platform_win as app


def test_fit_rect_to_work_area_shrinks_width():
    assert app.fit_rect_to_work_area(0, 20, 140, 60, (0, 0, 100, 100), (0, 0, 0, 0)) == (0, 20, 100, 60)


def test_fit_rect_to_work_area_shrinks_height():
    assert app.fit_rect_to_work_area(20, 0, 60, 140, (0, 0, 100, 100), (0, 0, 0, 0)) == (20, 0, 60, 100)


def test_fit_rect_to_work_area_moves_past_right_and_bottom():
    assert app.fit_rect_to_work_area(70, 80, 40, 30, (0, 0, 100, 100), (0, 0, 0, 0)) == (60, 70, 40, 30)


def test_fit_rect_to_work_area_moves_past_left_and_top():
    assert app.fit_rect_to_work_area(-10, -20, 40, 30, (0, 0, 100, 100), (0, 0, 0, 0)) == (0, 0, 40, 30)


def test_fit_rect_to_work_area_keeps_snapped_visible_edge():
    assert app.fit_rect_to_work_area(-7, 0, 114, 107, (0, 0, 100, 100), (7, 0, 7, 7)) == (-7, 0, 114, 107)


def test_fit_rect_to_work_area_keeps_already_fitting_rect():
    assert app.fit_rect_to_work_area(10, 20, 60, 50, (0, 0, 100, 100), (2, 3, 4, 5)) == (10, 20, 60, 50)


def test_fit_rect_to_work_area_handles_negative_origin():
    assert app.fit_rect_to_work_area(-180, 10, 80, 40, (-200, 0, 0, 100), (0, 0, 0, 0)) == (-180, 10, 80, 40)


def test_fit_rect_to_work_area_keeps_top_left_when_larger_in_both_directions():
    assert app.fit_rect_to_work_area(-20, -20, 140, 140, (0, 0, 100, 100), (0, 0, 0, 0)) == (0, 0, 100, 100)
