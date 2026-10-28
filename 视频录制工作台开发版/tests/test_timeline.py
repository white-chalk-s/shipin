import unittest

from timeline import output_to_source, source_to_output, total_frames, trim_to_output_range


class TimelineTests(unittest.TestCase):
    def test_removed_middle_and_half_open_boundaries(self):
        clips = [{"start": 0, "end": 3}, {"start": 6, "end": 9}]
        self.assertEqual(total_frames(clips), 6)
        self.assertEqual([output_to_source(clips, i) for i in range(6)], [0, 1, 2, 6, 7, 8])
        self.assertIsNone(source_to_output(clips, 3))
        self.assertEqual(source_to_output(clips, 6), 3)
        self.assertEqual(output_to_source(clips, 999), 8)

    def test_empty_and_trimmed_start(self):
        self.assertIsNone(output_to_source([], 0))
        self.assertEqual(output_to_source([{"start": 5, "end": 8}], 0), 5)

    def test_trim_range_across_deleted_gap(self):
        clips = [{"start": 0, "end": 3}, {"start": 6, "end": 10}]
        self.assertEqual(trim_to_output_range(clips, 1, 5), [{"start": 1, "end": 3}, {"start": 6, "end": 8}])
        self.assertEqual(trim_to_output_range(clips, 3, 3), [])


if __name__ == "__main__":
    unittest.main()
