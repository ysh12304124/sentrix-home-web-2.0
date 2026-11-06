#!/usr/bin/env python3
"""Produce product-format VLM observations for pre-extracted HippoVlog frames."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'apps/api/src')]
from sentrix.infrastructure.assistant.model_endpoint import ServedModel
from sentrix.infrastructure.media.vlm_observer import OpenAIVlmObserver

def main():
 p=argparse.ArgumentParser();p.add_argument('--frames-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--base-url',default='http://127.0.0.1:8300/v1');a=p.parse_args()
 observer=OpenAIVlmObserver(a.base_url,ServedModel(a.base_url),timeout_s=300)
 rows=[]
 for i,path in enumerate(sorted(a.frames_root.glob('*/kf_0001.jpg')),1):
  sem=observer.observe(path)
  rows.append({'asset_id':path.parent.name,'path':str(path),'caption':sem.caption if sem else '', 'activity':sem.activity if sem else '', 'place':sem.place if sem else '', 'event_type':sem.event_type if sem else '', 'objects':list(sem.objects) if sem else [], 'clothing':list(sem.clothing) if sem else [], 'ocr_text':sem.ocr_text if sem else ''})
  a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
  print(f'[{i}] {path.parent.name} {"ok" if sem else "empty"}',flush=True)
if __name__=='__main__':main()
