import unittest
import numpy as np

from speaker_embeddings import cluster


class SpeakerClusteringTests(unittest.TestCase):
    def test_more_than_two_speakers_and_returning_voice(self):
        self.assertEqual(cluster(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1],
                                          [1, .01, 0]])).tolist(), [0, 1, 2, 0])

    def test_single_voice_is_not_forced_into_two_clusters(self):
        self.assertEqual(cluster([[1, 0], [1, .01], [1, -.01]]).tolist(), [0, 0, 0])

    def test_empty_and_singleton(self):
        self.assertEqual(cluster([]).tolist(), [])
        self.assertEqual(cluster([[1, 0]]).tolist(), [0])
