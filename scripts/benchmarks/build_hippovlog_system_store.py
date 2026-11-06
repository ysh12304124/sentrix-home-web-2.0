#!/usr/bin/env python3
"""Write existing HippoVlog captions and Whisper segments into an isolated Sentrix store."""
from __future__ import annotations
import argparse, json, sys
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]; API=ROOT/'apps/api/src'; sys.path[:0]=[str(ROOT),str(API)]
from scripts.benchmarks.video_multimodal_retrieval_ab import DirectWeMM, MIGRATIONS
from sentrix.application.retrieval.index_audio_transcript import IndexAudioTranscriptSegments
from sentrix.application.retrieval.index_visual import IndexAssetVisual
from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.domain.media.models import Asset,AssetStatus,MediaType,Observation
from sentrix.domain.media.transcription import AudioTranscriptSegment,SegmentQualityStatus
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository,SqliteObservationRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

DEFAULT_SCOPE='hippovlog-system-whisper'
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--asr-root',type=Path,default=ROOT/'audio_eval/outputs/hippovlog_whisper');p.add_argument('--database',type=Path,required=True);p.add_argument('--model',default='weights/WeMM-Embedding-2B');p.add_argument('--device',default='mps');p.add_argument('--scope',default=DEFAULT_SCOPE);a=p.parse_args()
 db=Database(a.database,MIGRATIONS);db.migrate(); assets=SqliteAssetRepository(db);obs=SqliteObservationRepository(db);vectors=SqliteVectorRepository(db);emb=DirectWeMM(a.model,a.device)
 for vd in sorted(a.root.iterdir()):
  if not vd.is_dir() or not (vd/'observations.json').exists(): continue
  vid=vd.name; caps=json.loads((vd/'observations.json').read_text()); asr=json.loads((a.asr_root/vid/'asr.json').read_text())
  rows=[]
  for c in caps:
   path=Path(c['path']); aid=f'{vid}:scene:{c["asset_id"]}' ; now=datetime.now(UTC)
   asset=Asset(aid,a.scope,path.name,MediaType.IMAGE,str(path),'image/jpeg',path.stat().st_size,AssetStatus.PROCESSED,'',None,None,now,now)
   assets.add(asset); IndexAssetVisual(embedder=emb,vectors=vectors).index(asset)
   o=Observation(id=f'obs:{aid}',scope_id=a.scope,asset_id=aid,source_type='vlm',captured_at=None,confidence=.65,created_at=now,updated_at=now,caption=c['caption'],activity=c['activity'],place=c['place'],event_type=c['event_type'],ocr_text=c['ocr_text'],objects=tuple(c['objects']),clothing=tuple(c['clothing']))
   obs.add(o);IndexObservationText(embedder=emb,vectors=vectors).index(o,None)
  for i,s in enumerate(asr['segments']): rows.append(AudioTranscriptSegment(id=f'{vid}:whisper:{i:04d}',transcription_id=f'{vid}:whisper',asset_id=f'{vid}:scene:{caps[0]["asset_id"]}',scope_id=a.scope,ordinal=i,start_ms=s['start_ms'],end_ms=s['end_ms'],raw_text=s['raw_text'],normalized_text=s['normalized_text'],confidence_raw=None,confidence_calibrated=None,alignment_score=None,quality_status=SegmentQualityStatus.ACCEPTED,quality_reasons=(),searchable=True,local_speaker_label=''))
  IndexAudioTranscriptSegments(embedder=emb,vectors=vectors).index(rows[0].asset_id,rows)
  print(vid,len(caps),len(rows),flush=True)
if __name__=='__main__':main()
