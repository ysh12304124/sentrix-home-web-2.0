import { useState } from 'react';

function RequestContent({value}:{value:unknown}) {
  const images:string[]=[];
  const json=JSON.stringify(value, (_key,v:unknown)=> {
    if (typeof v === 'string' && v.startsWith('data:image/')) {
      const index=images.push(v); return '[图片附件 '+index+'，内容见下方]';
    }
    return v;
  },2);
  return <><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{json}</pre>{images.map((src,index)=> <img key={index} src={src} alt={'实际Judge图片附件 '+(index+1)} style={{maxWidth:'100%',maxHeight:400}} />)}</>;
}
export function ScoringRequestLog({requests,title='真实Judge输入与响应（逐次请求）'}:{requests:unknown;title?:string}) {
  const [open,setOpen]=useState(false);
  return <details onToggle={event=> {if(event.target===event.currentTarget)setOpen(event.currentTarget.open);}}><summary>{title}</summary>{open && <RequestContent value={requests} />}</details>;
}
