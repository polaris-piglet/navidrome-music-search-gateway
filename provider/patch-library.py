"""Apply narrowly scoped, fail-closed quality hooks to a private pinned module copy."""
from pathlib import Path
import sys
root=Path(sys.argv[1])
def replace(file,old,new):
 p=root/file;s=p.read_text();assert s.count(old)==1,(file,old);p.write_text(s.replace(old,new))
# Each request is one explicitly selected tier; platform authorization still applies.
replace('netease/download.go','levels := preferredDownloadLevels(s)', '''levels := preferredDownloadLevels(s)
 if level := s.Extra["gateway_quality"]; level != "" {
  u, ext, err := n.getEAPIDownloadURL(songID, level)
  if err == nil { s.Ext = ext }
  return u, err
 }''')
replace('qq/download.go','var filenames []string','''if level := s.Extra["gateway_quality"]; level != "" {
  switch level {
  case "flac": prefixes=[]string{"F000"}; exts=[]string{"flac"}
  case "320": prefixes=[]string{"M800"}; exts=[]string{"mp3"}
  case "128": prefixes=[]string{"M500"}; exts=[]string{"mp3"}
  default: return "", errors.New("unsupported quality")
  }
 }
 var filenames []string''')
replace('kuwo/download.go','return k.fetchAudioURL(rid)','return k.fetchAudioURL(rid, s.Extra["gateway_quality"])')
replace('kuwo/kuwo.go','func (k *Kuwo) fetchAudioURL(rid string) (string, error) {','func (k *Kuwo) fetchAudioURL(rid string, selected ...string) (string, error) {')
replace('kuwo/kuwo.go','qualities := []string{"128kmp3", "320kmp3", "flac", "2000kflac"}','''qualities := []string{"2000kflac", "flac", "320kmp3", "128kmp3"}
 if len(selected)>0 && selected[0]!="" { qualities=[]string{selected[0]} }''')
# Explicit tier failure must not silently take a lower-quality fallback.
replace('kuwo/kuwo.go','fallbackURL := fmt.Sprintf(','''if len(selected)>0 && selected[0]!="" { return "", fmt.Errorf("requested quality unavailable") }
 fallbackURL := fmt.Sprintf(''')
