"""Mouse wheel normalization.

The three platforms report scrolling three different ways:

  X11 (Tk 8.6)  <Button-4> / <Button-5> presses, no usable .delta
  Windows       <MouseWheel> with .delta in multiples of 120
  macOS         <MouseWheel> with small .delta values, often 1
  X11 (Tk 9)    <MouseWheel>, like Windows

The old code did ``-1 * (event.delta // 120)`` on <MouseWheel> only, which
never fired on Tk 8.6 X11 and floored macOS's small deltas to zero.
"""

import unittest

from helpers import app_module


class FakeEvent:
    """A wheel event. tkinter leaves .num as the string '??' when the
    event is not a button press."""

    def __init__(self, num="??", delta=0):
        self.num = num
        self.delta = delta


class WheelUnitTests(unittest.TestCase):
    def setUp(self):
        self.units = app_module().App._wheel_units

    # -- X11 / Tk 8.6 --
    def test_button_4_scrolls_up(self):
        self.assertEqual(self.units(FakeEvent(num=4)), -1)

    def test_button_5_scrolls_down(self):
        self.assertEqual(self.units(FakeEvent(num=5)), 1)

    # -- Windows --
    def test_windows_one_notch_up(self):
        self.assertEqual(self.units(FakeEvent(delta=120)), -1)

    def test_windows_one_notch_down(self):
        self.assertEqual(self.units(FakeEvent(delta=-120)), 1)

    def test_windows_multiple_notches(self):
        self.assertEqual(self.units(FakeEvent(delta=240)), -2)
        self.assertEqual(self.units(FakeEvent(delta=-360)), 3)

    # -- macOS --
    def test_macos_small_delta_is_not_divided_away(self):
        self.assertEqual(self.units(FakeEvent(delta=1)), -1)
        self.assertEqual(self.units(FakeEvent(delta=-1)), 1)

    def test_macos_trackpad_delta(self):
        self.assertEqual(self.units(FakeEvent(delta=-7)), 7)

    # -- degenerate input --
    def test_no_scroll_information_yields_zero(self):
        self.assertEqual(self.units(FakeEvent()), 0)

    def test_non_numeric_delta_yields_zero(self):
        self.assertEqual(self.units(FakeEvent(delta="??")), 0)

    def test_missing_attributes_yield_zero(self):
        class Bare:
            pass
        self.assertEqual(self.units(Bare()), 0)

    def test_an_unrelated_button_does_not_scroll(self):
        # Tk 9 renumbers X11 buttons, so <Button-4> can arrive as num=8.
        # Without a delta there is nothing to scroll by.
        self.assertEqual(self.units(FakeEvent(num=8)), 0)

    # -- direction invariants --
    def test_up_and_down_always_have_opposite_signs(self):
        pairs = [(FakeEvent(num=4), FakeEvent(num=5)),
                 (FakeEvent(delta=120), FakeEvent(delta=-120)),
                 (FakeEvent(delta=3), FakeEvent(delta=-3))]
        for up, down in pairs:
            with self.subTest(delta=up.delta, num=up.num):
                self.assertLess(self.units(up), 0)
                self.assertGreater(self.units(down), 0)


if __name__ == "__main__":
    unittest.main()
