"""Selected-track quality variants and measured quality ordering."""
import re

def variants(song):
    if song.get('source')=='kugou':
        seen=set()
        for key in ('res_hash','sq_hash','hq_hash','hash'):
            h=song.get('extra',{}).get(key)
            if h and re.fullmatch('[a-fA-F0-9]{32}',h) and h not in seen:
                seen.add(h)
                yield dict(song,id=h,url='',extra=dict(song.get('extra',{}),hash=h))
        if song.get('id') not in seen:yield song
    else:
        tiers={'netease':('hires','lossless','exhigh','standard'),
               'qq':('flac','320','128'),
               'kuwo':('2000kflac','flac','320kmp3','128kmp3')}
        for tier in tiers.get(song.get('source'),()):
            yield dict(song,url='',extra=dict(song.get('extra',{}),gateway_quality=tier))
        yield song  # Original provider fallback, still the same song ID.

def score(audio,fmt):
    codec=audio.get('codec_name','')
    lossless=codec in ('flac','alac')
    # Lossy bitrate is a deterministic preference, not a perceptual-quality claim.
    if lossless:return (1,int(audio.get('bits_per_raw_sample') or audio.get('bits_per_sample') or 0),int(audio.get('sample_rate') or 0),0)
    return (0,int(audio.get('bit_rate') or fmt.get('bit_rate') or 0),int(audio.get('sample_rate') or 0),0)
