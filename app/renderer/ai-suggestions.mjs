// Frozen AI rounds seed editable human copies; original AI output stays immutable.
export function installAISuggestions({ container, api, episodeId, seek, reload, applyDetail, notify }) {
  const panel=document.createElement('details');panel.className='ai-suggestions';container.before(panel);
  let generation=0;
  async function render(start=false) {
    const eid=episodeId(), g=++generation;panel.replaceChildren();if(!eid)return;
    const title=document.createElement('summary');title.textContent='AI 预标注 · 待人工复核';panel.append(title);
    for(const [label,action] of [['刷新本地 AI 结果',false]]) {
      const b=document.createElement('button');b.textContent=label;b.onclick=()=>render(action);panel.append(b);
    }
    const info=document.createElement('p');info.textContent='候选仅作提示；请检查完整视频并补标。';panel.append(info);
    if(!api.aiSuggestions){info.textContent='AI候选目前支持Web工作台。';return;}
    try {
      const data=await api.aiSuggestions(eid,start?'start':'suggestions',{});
      if(g!==generation||eid!==episodeId())return;
      if(data.local_cache_state && data.local_cache_state!=='ready') {
        panel.open=true;
        info.textContent=data.message||'AI 结果尚未缓存到本地，不代表没有标注。';
        return;
      }
      if(data.inherited_rounds){
        if(data.episode_detail && applyDetail) applyDetail(eid,data.episode_detail);
        else await reload();
        if(g!==generation||eid!==episodeId())return;
        info.textContent=`本地 AI 独立历史轮已载入时间轴 · 可离线修改、删除或补标；AI 原始记录保留。`;
        return;
      }
      const counts={};for(const c of data.coverage||[])counts[c.state]=(counts[c.state]||0)+1;
      info.textContent=(data.runs||[]).map(r=>({queued:'排队',running:'分析中',succeeded:'已就绪',failed:'失败',stale:'已过期'}[r.state]||r.state)).join(' / ')||'尚未生成，请在 Flow 质检工作台启动';
      info.textContent+=' · 无法判断 '+(counts.unknown||0)+' 项；未检出不等于合格。';
      if((data.candidates||[]).some(c=>c.state==='pending'))panel.open=true;
      for(const c of data.candidates||[]) {
        const a=c.annotation,row=document.createElement('div');row.className='ai-candidate';
        const title=document.createElement('button');title.textContent=`${a.label_code} ${(a.start_offset_ns/1e9).toFixed(3)}–${(a.end_offset_ns/1e9).toFixed(3)} 秒`;title.onclick=()=>seek(a.start_offset_ns);row.append(title);
        const evidence=document.createElement('p');evidence.textContent=a.comment||'';row.append(evidence);
        if(c.state==='accepted'||c.state==='rejected'){const s=document.createElement('small');s.textContent=c.state==='accepted'?'已确认（可在正式标注中继续修改）':'已排除';row.append(s);}
        else {
          const begin=document.createElement('input'),end=document.createElement('input');begin.type=end.type='number';begin.step=end.step='0.001';begin.value=(a.start_offset_ns/1e9).toFixed(9);end.value=(a.end_offset_ns/1e9).toFixed(9);begin.setAttribute('aria-label','AI候选开始秒');end.setAttribute('aria-label','AI候选结束秒');row.append(begin,end);
          for(const [text,action] of [['确认当前区间','accept'],['排除','reject']]){
            const b=document.createElement('button');b.textContent=text;b.onclick=async()=>{
              if(eid!==episodeId())return;
              b.disabled=true;
              try{
                const body={run_id:c.run_id,candidate_id:c.candidate_id,action};
                if(action==='accept')body.bounds={start_offset_ns:Math.round(Number(begin.value)*1e9),end_offset_ns:Math.round(Number(end.value)*1e9)};
                await api.aiSuggestions(eid,'review',body);if(eid===episodeId()){await reload();await render();}
              }catch(e){notify(e.message);b.disabled=false;}
            };row.append(b);
          }
        }
        panel.append(row);
      }
    } catch(e){if(g===generation)info.textContent=e.message||String(e);}
  }
  return render;
}
