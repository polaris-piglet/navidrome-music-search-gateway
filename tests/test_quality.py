import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from quality import variants,score
class QualityTests(unittest.TestCase):
 def test_same_selected_track_only(self):
  for source in ('qq','netease','kuwo'):
   song={'source':source,'id':'123','name':'Song (Live)','artist':'Singer','album':'Concert'}
   result=list(variants(song))
   self.assertGreater(len(result),1)
   for item in result:
    for field in ('source','id','name','artist','album'):self.assertEqual(item[field],song[field])
 def test_lossless_before_high_bitrate_lossy(self):
  self.assertGreater(score({'codec_name':'flac','sample_rate':'44100','bits_per_raw_sample':'16'},{}),score({'codec_name':'mp3','bit_rate':'640000'},{}))
 def test_lossy_and_lossless_ranking(self):
  self.assertGreater(score({'codec_name':'mp3','bit_rate':'320000'},{}),score({'codec_name':'mp3','bit_rate':'128000'},{}))
  self.assertGreater(score({'codec_name':'flac','bits_per_raw_sample':'24'},{}),score({'codec_name':'flac','bits_per_raw_sample':'16'},{}))
 def test_kugou_only_track_associated_hashes(self):
  song={'source':'kugou','id':'a'*32,'extra':{'hash':'a'*32,'sq_hash':'b'*32,'mv_hash':'c'*32}}
  self.assertEqual([x['id'] for x in variants(song)],['b'*32,'a'*32])
