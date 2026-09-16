import {nearestFrame, calibrationBounds} from './annotation-calibration.mjs';
import {framePositionForTime} from './range-selection.mjs';

export function zoomWindow(start,end,anchor,factor,duration){
  const width=Math.min(duration,Math.max(Math.min(duration,1e8),(end-start)*factor));
  const ratio=(anchor-start)/Math.max(1,end-start);
  const left=Math.max(0,Math.min(duration-width,anchor-ratio*width));
  return [left,left+width];
}

export function draggedBounds(annotation,edge,value,limit){
  if(annotation.scope==='time_point')return calibrationBounds('time_point',value,value,limit);
  if(annotation.scope!=='time_range')throw new Error('整条标签不能拖动修改时间');
  return calibrationBounds('time_range',edge==='start'?value:annotation.start_offset_ns,edge==='end'?value:annotation.end_offset_ns,limit);
}

export function contiguousAiSegments(annotations,duration){
  const limit=Number(duration)||0;
  const values=(annotations||[]).filter(a=>{
    const attributes=a?.attributes||{};
    return a?.scope==='time_range'&&(attributes.ai_provenance||attributes._incremental_source?.round_kind==='ai');
  }).sort((a,b)=>Number(a.start_offset_ns)-Number(b.start_offset_ns)||Number(a.end_offset_ns)-Number(b.end_offset_ns));
  if(!limit||!values.length||Number(values[0].start_offset_ns)<0||Number(values.at(-1).end_offset_ns)>limit)return [];
  if(Number(values[0].start_offset_ns)>=Number(values[0].end_offset_ns)||Number(values.at(-1).start_offset_ns)>=Number(values.at(-1).end_offset_ns))return [];
  for(let i=1;i<values.length;i++)if(Number(values[i-1].end_offset_ns)!==Number(values[i].start_offset_ns))return [];
  return values;
}

export function isPrimaryAiSegment(annotation,label={}){
  const attributes=annotation?.attributes||{};
  const isAi=Boolean(attributes.ai_provenance||attributes._incremental_source?.round_kind==='ai');
  const role=String(attributes.ai_provenance?.segment_role||attributes.ai_provenance?.track_kind||'').toLowerCase();
  const group=String(label?.group||annotation?.annotation_type||'').toLowerCase();
  return isAi&&annotation?.scope==='time_range'
    &&(role==='phase'||role==='segmentation'||/(^|_)(phase|action|step)($|_)/.test(group));
}

export function contiguousAiSegmentGroup(annotations,labels,duration){
  const limit=Number(duration)||0;
  const groups=new Map();
  for(const annotation of annotations||[]){
    const attributes=annotation?.attributes||{};
    if(annotation?.scope!=='time_range'||!(attributes.ai_provenance||attributes._incremental_source?.round_kind==='ai'))continue;
    const label=labels?.get?.(annotation.label_code)||{};
    const group=String(label.group||label.group_code||attributes.ai_provenance?.segment_role||'').trim();
    const run=String(attributes.ai_provenance?.run_id||attributes._incremental_source?.review_attempt_id||'').trim();
    const key=`${group||'ungrouped'}\u0000${run||'unknown-run'}`;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(annotation);
  }
  return [...groups.values()]
    .map(values=>{
      const ordered=values.slice().sort((a,b)=>Number(a.start_offset_ns)-Number(b.start_offset_ns)||Number(a.end_offset_ns)-Number(b.end_offset_ns));
      if(!limit||ordered.length<2)return [];
      if(Number(ordered[0].start_offset_ns)<0||Number(ordered.at(-1).end_offset_ns)>limit)return [];
      let uncovered=0;
      for(let i=1;i<ordered.length;i++){
        const gap=Number(ordered[i].start_offset_ns)-Number(ordered[i-1].end_offset_ns);
        if(gap<0||gap>limit*.02)return [];
        uncovered+=gap;
      }
      if(uncovered>limit*.03)return [];
      return ordered.map((annotation,index)=>({
        ...annotation,
        start_offset_ns:Number(annotation.start_offset_ns),
        end_offset_ns:index===ordered.length-1?Number(annotation.end_offset_ns):Number(ordered[index+1].start_offset_ns),
      }));
    })
    .filter(values=>values.length)
    .sort((left,right)=>right.length-left.length)[0]||[];
}

export function sharedBoundaryBounds(left,right,value,limit){
  if(left?.scope!=='time_range'||right?.scope!=='time_range')throw new Error('共享分界点仅支持区间标注');
  if(left.episode_id!==right.episode_id||Number(left.end_offset_ns)>Number(right.start_offset_ns))throw new Error('相邻 AI 分段重叠或顺序无效');
  const boundary=Math.round(Number(value));
  if(!Number.isFinite(boundary)||boundary<=Number(left.start_offset_ns)||boundary>=Number(right.end_offset_ns)||boundary<0||boundary>Number(limit))throw new Error('分界点必须位于相邻两段内部');
  return {
    left:{start_offset_ns:Number(left.start_offset_ns),end_offset_ns:boundary},
    right:{start_offset_ns:boundary,end_offset_ns:Number(right.end_offset_ns)},
  };
}

export function outerBoundaryBounds(annotation,edge,value,limit){
  if(annotation?.scope!=='time_range')throw new Error('首尾分界点仅支持区间标注');
  const boundary=Math.round(Number(value)),maximum=Number(limit);
  if(!Number.isFinite(boundary)||boundary<0||boundary>maximum)throw new Error('分界点超出数据范围');
  if(edge==='start'){
    if(boundary>=Number(annotation.end_offset_ns))throw new Error('首段起点必须早于该段终点');
    return {start_offset_ns:boundary,end_offset_ns:Number(annotation.end_offset_ns)};
  }
  if(edge==='end'){
    if(boundary<=Number(annotation.start_offset_ns))throw new Error('末段终点必须晚于该段起点');
    return {start_offset_ns:Number(annotation.start_offset_ns),end_offset_ns:boundary};
  }
  throw new Error('未知的首尾分界点');
}

export function nearestValidOuterBoundary(offsets,value,annotation,edge,limit){
  const hardEdge=edge==='start'?0:Number(limit);
  const candidates=[...(offsets||[]).map(Number),hardEdge].filter((frame,index,values)=>values.indexOf(frame)===index).filter(frame=>{
    if(!Number.isFinite(frame)||frame<0||frame>Number(limit))return false;
    return edge==='start'?frame<Number(annotation.end_offset_ns):frame>Number(annotation.start_offset_ns);
  });
  if(!candidates.length)return null;
  return candidates.reduce((best,frame)=>Math.abs(frame-Number(value))<Math.abs(best-Number(value))?frame:best,candidates[0]);
}

export function adjacentFrameBoundary(offsets,current,direction,left,right){
  const frames=(offsets||[]).map(Number).filter(Number.isFinite).sort((a,b)=>a-b);
  if(!frames.length)return null;
  const nearest=nearestFrame(frames,Number(current));
  const index=frames.indexOf(nearest),step=direction<0?-1:1;
  for(let i=index+step;i>=0&&i<frames.length;i+=step){
    if(frames[i]>Number(left.start_offset_ns)&&frames[i]<Number(right.end_offset_ns))return frames[i];
  }
  return null;
}

export function nearestValidBoundary(offsets,value,left,right){
  const minimum=Number(left.start_offset_ns),maximum=Number(right.end_offset_ns);
  const candidates=(offsets||[]).map(Number).filter(frame=>Number.isFinite(frame)&&frame>minimum&&frame<maximum);
  if(!candidates.length)return null;
  return candidates.reduce((best,frame)=>Math.abs(frame-Number(value))<Math.abs(best-Number(value))?frame:best,candidates[0]);
}

export function timeAtClientX(view,clientX,rect,grabOffsetX=0){
  return view[0]+Math.max(0,Math.min(1,(clientX-grabOffsetX-rect.left)/Math.max(1,rect.width)))*(view[1]-view[0]);
}

export function boundaryGrabOffset(view,boundary,clientX,rect){
  const ratio=Math.max(0,Math.min(1,(Number(boundary)-view[0])/Math.max(1,view[1]-view[0])));
  return clientX-(rect.left+ratio*rect.width);
}

export function installIntervalTrack({container,getState,seek,pause,save,saveBoundary,edit,notify}){
  const toolbar=document.createElement('div');toolbar.className='interval-track-tools';
  toolbar.innerHTML='<div class="interval-track-tool-group" role="group" aria-label="时间轴视图"><button type="button" data-view="in" title="以播放点为中心放大">放大</button><button type="button" data-view="out" title="缩小时间轴">缩小</button><button type="button" data-view="left" title="向前平移">前移</button><button type="button" data-view="right" title="向后平移">后移</button><button type="button" data-view="all" title="显示完整 Episode">适应全段</button></div><span class="interval-track-status" role="status"></span>';
  container.before(toolbar);
  const inspector=document.createElement('section');inspector.className='ai-boundary-inspector';inspector.hidden=true;
  inspector.setAttribute('aria-label','AI 分界点精调');
  inspector.innerHTML='<div class="ai-boundary-pair"><small>调整分界</small><strong><span data-boundary-left-name></span><b>→</b><span data-boundary-right-name></span></strong></div><div class="ai-boundary-time"><small>分界帧</small><div><button type="button" data-boundary-nudge="-1" aria-label="向前一帧" title="向前一帧">‹</button><strong data-boundary-frame>F--</strong><button type="button" data-boundary-nudge="1" aria-label="向后一帧" title="向后一帧">›</button></div></div><details class="ai-boundary-details"><summary>更多信息</summary><div><label title="对应精确时间"><span>精确时间</span><input data-boundary-seconds type="number" min="0" step="0.001" inputmode="decimal" aria-label="分界时间（秒）"><em>s</em></label><p class="ai-boundary-durations"><span data-left-duration></span><b>·</b><span data-right-duration></span></p><small>自动吸附到视频帧</small></div></details>';
  container.after(inspector);
  let episode=null,duration=0,view=[0,1],selected=null,selectedBoundary=null,drag=null,saving=false,suppressClick=false;
  const status=toolbar.querySelector('[role="status"]'),secondsInput=inspector.querySelector('[data-boundary-seconds]');
  function time(event,surface,grabOffsetX=0){return timeAtClientX(view,event.clientX,surface.getBoundingClientRect(),grabOffsetX);}
  function reset(){episode=getState().episodeId;duration=getState().duration;view=[0,Math.max(1,duration)];selected=null;selectedBoundary=null;drag=null;}
  function activeBoundary(annotations){
    const ids=drag?.kind==='boundary'?{leftId:drag.leftId,rightId:drag.rightId}:selectedBoundary;
    if(!ids)return null;
    let left=drag?.kind==='boundary'?drag.leftDraft:annotations.get(ids.leftId),right=drag?.kind==='boundary'?drag.rightDraft:annotations.get(ids.rightId);
    if(!left||!right)return null;
    if(drag?.kind!=='boundary'){
      const handle=container.querySelector(`[data-boundary-left-id="${ids.leftId}"][data-boundary-right-id="${ids.rightId}"]`);
      const boundary=Number(handle?.dataset.boundaryOffsetNs);
      if(Number.isFinite(boundary)){left={...left,end_offset_ns:boundary};right={...right,start_offset_ns:boundary};}
    }
    return {...ids,left,right};
  }
  function labelName(annotation){return annotation?.label_name||annotation?.label_code||'未命名分段';}
  function renderInspector(annotations,s){
    const pair=activeBoundary(annotations);
    if(!pair){inspector.hidden=true;return;}
    inspector.hidden=false;
    inspector.classList.toggle('saving',saving);
    const boundary=Number(pair.left.end_offset_ns),offsets=s.grid?.exact?s.grid.frameOffsetsNs:null;
    const snapped=offsets?.length?nearestFrame(offsets,boundary):boundary;
    const frameIndex=offsets?.length?offsets.indexOf(snapped):-1;
    inspector.querySelector('[data-boundary-left-name]').textContent=labelName(pair.left);
    inspector.querySelector('[data-boundary-right-name]').textContent=labelName(pair.right);
    if(document.activeElement!==secondsInput)secondsInput.value=(boundary/1e9).toFixed(6);
    secondsInput.disabled=saving;
    inspector.querySelector('[data-boundary-frame]').textContent=frameIndex>=0?`F${frameIndex+1}`:'F--';
    const leftFrames=offsets?.length?offsets.filter(value=>value>=Number(pair.left.start_offset_ns)&&value<boundary).length:null;
    const rightFrames=offsets?.length?offsets.filter(value=>value>=boundary&&value<Number(pair.right.end_offset_ns)).length:null;
    inspector.querySelector('[data-left-duration]').textContent=leftFrames===null?`左 ${((boundary-Number(pair.left.start_offset_ns))/1e9).toFixed(3)}s`:`左 ${leftFrames}帧`;
    inspector.querySelector('[data-right-duration]').textContent=rightFrames===null?`右 ${((Number(pair.right.end_offset_ns)-boundary)/1e9).toFixed(3)}s`:`右 ${rightFrames}帧`;
    for(const button of inspector.querySelectorAll('[data-boundary-nudge]'))button.disabled=saving||!offsets?.length||adjacentFrameBoundary(offsets,boundary,Number(button.dataset.boundaryNudge),pair.left,pair.right)===null;
  }
  function render(){
    const s=getState();if(episode!==s.episodeId||duration!==s.duration)reset();
    toolbar.hidden=!s.episodeId||!s.duration;
    const displayTime=drag?.kind==='boundary'?Number(drag.leftDraft.end_offset_ns):drag?.kind==='outer-boundary'?Number(drag.edge==='start'?drag.draft.start_offset_ns:drag.draft.end_offset_ns):s.time;
    // Reuse the original scrubber and its ticks with the same visible window.
    const scrubber=container.parentElement.querySelector('#timeline-range');
    if(scrubber)scrubber.value=String(Math.max(0,Math.min(1,(displayTime-view[0])/(view[1]-view[0])))*1e6);
    const ticks=container.parentElement.querySelectorAll('.timeline-ticks span');
    ticks.forEach((tick,i)=>{const value=view[0]+(view[1]-view[0])*i/Math.max(1,ticks.length-1),position=framePositionForTime(value,duration,s.grid);tick.textContent=position?`${position.exact?'':'≈'}F${position.number}`:`${(value/1e9).toFixed(3)}s`;tick.title=`${(value/1e9).toFixed(3)}s`;});
    const annotations=new Map(s.annotations.map(a=>[a.annotation_id,a]));
    const boundary=activeBoundary(annotations);
    for(const block of container.querySelectorAll('[data-annotation-id]')){
      const a=annotations.get(block.dataset.annotationId);if(!a)continue;
      const value=drag?.kind==='boundary'?(drag.leftId===a.annotation_id?drag.leftDraft:drag.rightId===a.annotation_id?drag.rightDraft:a):(drag?.id===a.annotation_id?drag.draft:a);
      const aiBlock=block.closest('[data-ai-segment-track]');
      const displayStart=Number(block.dataset.displayStartNs),displayEnd=Number(block.dataset.displayEndNs);
      let start=aiBlock&&Number.isFinite(displayStart)?displayStart:value.start_offset_ns;
      let end=aiBlock&&Number.isFinite(displayEnd)?displayEnd:value.end_offset_ns;
      if(drag?.kind==='boundary'&&drag.leftId===a.annotation_id)end=drag.leftDraft.end_offset_ns;
      if(drag?.kind==='boundary'&&drag.rightId===a.annotation_id)start=drag.rightDraft.start_offset_ns;
      if(drag?.kind==='outer-boundary'&&drag.id===a.annotation_id){start=drag.draft.start_offset_ns;end=drag.draft.end_offset_ns;}
      const width=view[1]-view[0];
      block.hidden=end<view[0]||start>view[1];
      block.style.setProperty('--annotation-left',`${100*(Math.max(start,view[0])-view[0])/width}%`);
      block.style.setProperty('--annotation-width',`${100*Math.max(0,Math.min(end,view[1])-Math.max(start,view[0]))/width}%`);
      block.classList.toggle('interval-selected',a.annotation_id===selected);
      block.classList.toggle('boundary-adjacent',Boolean(boundary&&(a.annotation_id===boundary.leftId||a.annotation_id===boundary.rightId)));
      block.classList.toggle('boundary-left',Boolean(boundary&&a.annotation_id===boundary.leftId));
      block.classList.toggle('boundary-right',Boolean(boundary&&a.annotation_id===boundary.rightId));
      block.classList.toggle('draggable-time-point',a.scope==='time_point');
      const editable=a.annotation_id===selected&&a.scope==='time_range'&&!block.closest('[data-ai-segment-track]');
      for(const edge of ['start','end']){
        let handle=block.querySelector(`[data-edge="${edge}"]`);
        if(!handle&&editable){handle=document.createElement('span');handle.dataset.edge=edge;handle.className='interval-handle';handle.setAttribute('aria-label',edge==='start'?'拖动起点':'拖动终点');block.append(handle);}
        if(handle)handle.hidden=!editable||saving||(edge==='start'?start<view[0]:end>view[1]);
      }
    }
    for(const handle of container.querySelectorAll('[data-boundary-left-id]')){
      const left=drag?.kind==='boundary'&&drag.leftId===handle.dataset.boundaryLeftId?drag.leftDraft:annotations.get(handle.dataset.boundaryLeftId);
      if(!left)continue;
      const visualBoundary=Number(handle.dataset.boundaryOffsetNs);
      const boundary=drag?.kind==='boundary'&&drag.leftId===handle.dataset.boundaryLeftId?Number(left.end_offset_ns):(Number.isFinite(visualBoundary)?visualBoundary:Number(left.end_offset_ns));
      handle.hidden=saving||boundary<view[0]||boundary>view[1];
      handle.style.setProperty('--boundary-left',`${100*(boundary-view[0])/(view[1]-view[0])}%`);
      handle.classList.toggle('active',Boolean(selectedBoundary&&handle.dataset.boundaryLeftId===selectedBoundary.leftId&&handle.dataset.boundaryRightId===selectedBoundary.rightId));
    }
    for(const handle of container.querySelectorAll('[data-outer-boundary-edge]')){
      const annotation=drag?.kind==='outer-boundary'&&drag.id===handle.dataset.outerBoundaryAnnotationId?drag.draft:annotations.get(handle.dataset.outerBoundaryAnnotationId);
      if(!annotation)continue;
      const edge=handle.dataset.outerBoundaryEdge,boundary=Number(edge==='start'?annotation.start_offset_ns:annotation.end_offset_ns);
      handle.hidden=saving||boundary<view[0]||boundary>view[1];
      handle.style.setProperty('--boundary-left',`${100*(boundary-view[0])/(view[1]-view[0])}%`);
      handle.classList.toggle('active',drag?.kind==='outer-boundary'&&drag.id===annotation.annotation_id&&drag.edge===edge);
    }
    for(const surface of container.querySelectorAll('.annotation-lane-surface')){
      let head=surface.querySelector('.interval-playhead');if(!head){head=document.createElement('i');head.className='interval-playhead';surface.append(head);}
      head.hidden=displayTime<view[0]||displayTime>view[1];head.style.left=`${100*(displayTime-view[0])/(view[1]-view[0])}%`;
    }
    const a=drag?.kind==='boundary'||drag?.kind==='outer-boundary'?null:(drag?.draft||annotations.get(selected));
    const offsets=s.grid?.exact?s.grid.frameOffsetsNs:null;
    const frame=t=>offsets?` F${offsets.indexOf(nearestFrame(offsets,t))+1}`:'';
    const boundaryDetail=drag?.kind==='boundary'?` · AI 分界点${frame(drag.leftDraft.end_offset_ns)} · ${(drag.leftDraft.end_offset_ns/1e9).toFixed(6)}s · 左右两段联动，松开保存，Esc取消`:'';
    const outerDetail=drag?.kind==='outer-boundary'?` · ${drag.edge==='start'?'首段起点':'末段终点'}${frame(drag.edge==='start'?drag.draft.start_offset_ns:drag.draft.end_offset_ns)} · ${((drag.edge==='start'?drag.draft.start_offset_ns:drag.draft.end_offset_ns)/1e9).toFixed(6)}s · 松开保存，Esc取消`:'';
    const detail=boundaryDetail||outerDetail||(a?.scope==='time_point'?` · 时间点 ${(a.start_offset_ns/1e9).toFixed(6)}s${frame(a.start_offset_ns)} · 拖动标记修改，松开保存，Esc取消`:a?` · ${(a.start_offset_ns/1e9).toFixed(6)}s${frame(a.start_offset_ns)} → ${(a.end_offset_ns/1e9).toFixed(6)}s${frame(a.end_offset_ns)} · 持续 ${((a.end_offset_ns-a.start_offset_ns)/1e9).toFixed(6)}s · 拖动两端修改，松开保存，Esc取消`:' · 点击标注选中');
    const firstFrame=framePositionForTime(view[0],duration,s.grid),lastFrame=framePositionForTime(view[1],duration,s.grid);
    const viewLabel=firstFrame&&lastFrame?`${firstFrame.exact?'':'≈'}F${firstFrame.number}–F${lastFrame.number} · ${(view[0]/1e9).toFixed(3)}–${(view[1]/1e9).toFixed(3)}s`:`${(view[0]/1e9).toFixed(3)}–${(view[1]/1e9).toFixed(3)}s`;
    status.textContent=saving?'正在保存分界点…':`${viewLabel} · 滚轮缩放 · Shift+滚轮平移`+detail+` · ${s.grid?.displayName||'时间定位'} · I/O 设置区间`;
    renderInspector(annotations,s);
  }
  function changeView(action,anchor=getState().time){
    if(drag||!getState().duration)return;const s=getState(),width=view[1]-view[0];
    if(action==='all')view=[0,Math.max(1,s.duration)];
    else if(action==='left'||action==='right'){const left=Math.max(0,Math.min(s.duration-width,view[0]+(action==='left'?-1:1)*width*.25));view=[left,left+width];}
    else view=zoomWindow(...view,Math.max(view[0],Math.min(view[1],anchor)),action==='in'?.5:2,s.duration);
    render();
  }
  toolbar.onclick=e=>{const action=e.target.closest('[data-view]')?.dataset.view;if(action)changeView(action);};
  async function commitBoundary(value){
    if(saving||!selectedBoundary||!saveBoundary)return;
    const s=getState(),left=s.annotations.find(a=>a.annotation_id===selectedBoundary.leftId),right=s.annotations.find(a=>a.annotation_id===selectedBoundary.rightId);
    if(!left||!right){selectedBoundary=null;render();return;}
    const offsets=s.grid?.exact?s.grid.frameOffsetsNs:null;
    const snapped=offsets?.length?nearestValidBoundary(offsets,value,left,right):null;
    const boundary=Math.round(snapped??value);
    try{sharedBoundaryBounds(left,right,boundary,s.limit);}catch(error){notify(error.message||'分界点无效');render();return;}
    if(boundary===Number(left.end_offset_ns)){seek(boundary);render();return;}
    saving=true;pause();seek(boundary);render();
    try{await saveBoundary(structuredClone(left),structuredClone(right),boundary);}
    catch(error){notify(error.message||'保存失败，已恢复原分界点');}
    finally{saving=false;render();}
  }
  inspector.addEventListener('click',e=>{
    const button=e.target.closest('[data-boundary-nudge]');if(!button||button.disabled)return;
    const s=getState(),annotations=new Map(s.annotations.map(a=>[a.annotation_id,a])),pair=activeBoundary(annotations),offsets=s.grid?.exact?s.grid.frameOffsetsNs:null;
    if(!pair||!offsets?.length)return;
    const value=adjacentFrameBoundary(offsets,pair.left.end_offset_ns,Number(button.dataset.boundaryNudge),pair.left,pair.right);
    if(value!==null)commitBoundary(value);
  });
  secondsInput.addEventListener('change',()=>{const value=Number(secondsInput.value)*1e9;if(Number.isFinite(value))commitBoundary(value);else render();});
  secondsInput.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();secondsInput.blur();}else if(e.key==='Escape'){e.preventDefault();render();secondsInput.blur();}});
  container.addEventListener('wheel',e=>{if(!getState().episodeId)return;e.preventDefault();const surface=e.target.closest('.annotation-lane-surface')||container.querySelector('.annotation-lane-surface');if(!surface)return;changeView(e.shiftKey?(e.deltaY<0?'left':'right'):(e.deltaY<0?'in':'out'),time(e,surface));},{passive:false});
  container.addEventListener('pointerdown',e=>{
    if(e.button!==0||saving)return;
    const outerHandle=e.target.closest('[data-outer-boundary-edge]');
    if(outerHandle){
      const s=getState(),annotation=s.annotations.find(a=>a.annotation_id===outerHandle.dataset.outerBoundaryAnnotationId),edge=outerHandle.dataset.outerBoundaryEdge;
      if(!annotation||!save)return;
      const boundary=Number(edge==='start'?annotation.start_offset_ns:annotation.end_offset_ns);
      e.preventDefault();e.stopImmediatePropagation();pause();selected=annotation.annotation_id;selectedBoundary=null;
      const rect=outerHandle.closest('.annotation-lane-surface').getBoundingClientRect();
      drag={kind:'outer-boundary',id:annotation.annotation_id,original:structuredClone(annotation),draft:{...annotation},edge,episode:s.episodeId,pointer:e.pointerId,surface:{getBoundingClientRect:()=>rect},grabOffsetX:boundaryGrabOffset(view,boundary,e.clientX,rect),changed:false};
      container.setPointerCapture(e.pointerId);render();return;
    }
    const boundaryHandle=e.target.closest('[data-boundary-left-id]');
    if(boundaryHandle){
      const s=getState(),left=s.annotations.find(a=>a.annotation_id===boundaryHandle.dataset.boundaryLeftId),right=s.annotations.find(a=>a.annotation_id===boundaryHandle.dataset.boundaryRightId);
      if(!left||!right||!saveBoundary)return;
      e.preventDefault();e.stopImmediatePropagation();pause();selected=null;selectedBoundary={leftId:left.annotation_id,rightId:right.annotation_id};
      const rect=boundaryHandle.closest('.annotation-lane-surface').getBoundingClientRect();
      const visualBoundary=Number(boundaryHandle.dataset.boundaryOffsetNs)||Number(right.start_offset_ns);
      drag={kind:'boundary',leftId:left.annotation_id,rightId:right.annotation_id,leftOriginal:structuredClone(left),rightOriginal:structuredClone(right),leftDraft:{...left,end_offset_ns:visualBoundary},rightDraft:{...right,start_offset_ns:visualBoundary},episode:s.episodeId,pointer:e.pointerId,surface:{getBoundingClientRect:()=>rect},grabOffsetX:boundaryGrabOffset(view,visualBoundary,e.clientX,rect),changed:false};
      container.setPointerCapture(e.pointerId);render();return;
    }
    const block=e.target.closest('[data-annotation-id]');if(!block)return;
    const id=block.dataset.annotationId,a=getState().annotations.find(a=>a.annotation_id===id);
    const handle=e.target.closest('[data-edge]');
    if(!a||!(a.scope==='time_point'||(a.scope==='time_range'&&handle)))return;
    e.preventDefault();e.stopImmediatePropagation();pause();selected=id;
    const rect=block.closest('.annotation-lane-surface').getBoundingClientRect();
    drag={kind:'annotation',id,original:structuredClone(a),draft:{...a},edge:a.scope==='time_point'?'point':handle.dataset.edge,anchorX:e.clientX,episode:getState().episodeId,pointer:e.pointerId,surface:{getBoundingClientRect:()=>rect},changed:false};
    container.setPointerCapture(e.pointerId);
    render();
  },true);
  container.addEventListener('pointermove',e=>{
    if(!drag||e.pointerId!==drag.pointer)return;
    if(getState().episodeId!==drag.episode){cancel();return;}
    if(drag.edge==='point'&&!drag.changed&&Math.abs(e.clientX-drag.anchorX)<4)return;
    const s=getState(),raw=time(e,drag.surface,drag.kind==='boundary'||drag.kind==='outer-boundary'?drag.grabOffsetX:0);
    if(drag.kind==='boundary'){
      const value=Math.round(raw);
      let bounds;try{bounds=sharedBoundaryBounds(drag.leftOriginal,drag.rightOriginal,value,s.limit);}catch{return;}
      drag.leftDraft={...drag.leftOriginal,...bounds.left};drag.rightDraft={...drag.rightOriginal,...bounds.right};drag.changed=value!==Number(drag.leftOriginal.end_offset_ns);
      // Keep the pre-redesign manual calibration behaviour: the playhead,
      // cameras and robot data follow the pointer while only the final value
      // is persisted on pointerup. requestVisualFrames coalesces these seeks.
      seek(value);render();return;
    }
    if(drag.kind==='outer-boundary'){
      const value=Math.round(raw);
      let bounds;try{bounds=outerBoundaryBounds(drag.original,drag.edge,value,s.limit);}catch{return;}
      drag.draft={...drag.original,...bounds};drag.changed=value!==Number(drag.edge==='start'?drag.original.start_offset_ns:drag.original.end_offset_ns);
      seek(value);render();return;
    }
    const value=Math.round(nearestFrame(s.grid?.exact?s.grid.frameOffsetsNs:null,raw));
    let bounds;try{bounds=draggedBounds(drag.draft,drag.edge,value,s.limit);}catch{return;}
    drag.draft={...drag.draft,...bounds};drag.changed=bounds.start_offset_ns!==drag.original.start_offset_ns||bounds.end_offset_ns!==drag.original.end_offset_ns;seek(value);render();
  });
  function cancel(){if(drag){const pointer=drag.pointer;drag=null;if(container.hasPointerCapture(pointer))container.releasePointerCapture(pointer);}render();}
  container.addEventListener('pointercancel',cancel);
  container.addEventListener('lostpointercapture',()=>{if(drag)cancel();});
  window.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&drag){e.preventDefault();cancel();return;}
    if(e.key==='Escape'&&selectedBoundary){e.preventDefault();selectedBoundary=null;render();return;}
    if(selectedBoundary&&!saving&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)&&(e.key==='ArrowLeft'||e.key==='ArrowRight')){
      const s=getState(),annotations=new Map(s.annotations.map(a=>[a.annotation_id,a])),pair=activeBoundary(annotations),offsets=s.grid?.exact?s.grid.frameOffsetsNs:null;
      if(!pair||!offsets?.length)return;e.preventDefault();
      const value=adjacentFrameBoundary(offsets,pair.left.end_offset_ns,e.key==='ArrowLeft'?-1:1,pair.left,pair.right);if(value!==null)commitBoundary(value);
    }
  });
  container.addEventListener('pointerup',async e=>{
    if(!drag||e.pointerId!==drag.pointer)return;
    const done=drag;drag=null;suppressClick=true;setTimeout(()=>suppressClick=false,0);
    if(container.hasPointerCapture(e.pointerId))container.releasePointerCapture(e.pointerId);
    if(!done.changed||getState().episodeId!==done.episode){if(!done.changed&&done.edge==='point'&&getState().episodeId===done.episode)seek(done.original.start_offset_ns);render();return;}
    if(done.kind==='boundary'){
      const s=getState(),offsets=s.grid?.exact?s.grid.frameOffsetsNs:null;
      const snapped=offsets?.length?nearestValidBoundary(offsets,done.leftDraft.end_offset_ns,done.leftOriginal,done.rightOriginal):null;
      const boundary=Math.round(snapped??done.leftDraft.end_offset_ns);
      if(boundary===Number(done.leftOriginal.end_offset_ns)){seek(boundary);render();return;}
      const bounds=sharedBoundaryBounds(done.leftOriginal,done.rightOriginal,boundary,s.limit);
      done.leftDraft={...done.leftOriginal,...bounds.left};done.rightDraft={...done.rightOriginal,...bounds.right};
      seek(boundary);
    }
    if(done.kind==='outer-boundary'){
      const s=getState(),offsets=s.grid?.exact?s.grid.frameOffsetsNs:null;
      const current=Number(done.edge==='start'?done.draft.start_offset_ns:done.draft.end_offset_ns);
      const snapped=offsets?.length?nearestValidOuterBoundary(offsets,current,done.original,done.edge,s.limit):null;
      const boundary=Math.round(snapped??current),originalBoundary=Number(done.edge==='start'?done.original.start_offset_ns:done.original.end_offset_ns);
      if(boundary===originalBoundary){seek(boundary);render();return;}
      done.draft={...done.original,...outerBoundaryBounds(done.original,done.edge,boundary,s.limit)};
      seek(boundary);
    }
    saving=true;render();
    try{
      if(done.kind==='boundary')await saveBoundary(done.leftOriginal,done.rightOriginal,done.leftDraft.end_offset_ns);
      else if(done.kind==='outer-boundary')await save(done.original,outerBoundaryBounds(done.original,done.edge,done.edge==='start'?done.draft.start_offset_ns:done.draft.end_offset_ns,getState().limit));
      else await save(done.original,calibrationBounds(done.original.scope,done.draft.start_offset_ns,done.draft.end_offset_ns,getState().limit));
    }
    catch(error){notify(error.message||'保存失败，已恢复原区间');}finally{saving=false;render();}
  });
  container.addEventListener('click',e=>{
    if(suppressClick||saving)return;const block=e.target.closest('[data-annotation-id]');
    if(block&&block.closest('[data-ai-segment-track]')){selected=null;selectedBoundary=null;const surface=block.closest('.annotation-lane-surface');pause();seek(time(e,surface));render();}
    else if(block){selectedBoundary=null;selected=block.dataset.annotationId;const a=getState().annotations.find(a=>a.annotation_id===selected);if(a){pause();seek(a.start_offset_ns);}render();}
    else{const surface=e.target.closest('.annotation-lane-surface');if(surface){selectedBoundary=null;pause();seek(time(e,surface));render();}}
  });
  container.addEventListener('dblclick',e=>{
    if(saving)return;
    const block=e.target.closest('[data-annotation-id]');
    if(!block||!edit)return;
    e.preventDefault();e.stopImmediatePropagation();pause();edit(block.dataset.annotationId);
  });
  return {render,time,atRatio(ratio){return view[0]+Math.max(0,Math.min(1,ratio))*(view[1]-view[0]);},select(id){selected=id;render();}};
}
