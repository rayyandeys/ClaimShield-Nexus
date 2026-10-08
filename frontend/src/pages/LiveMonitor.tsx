import {useEffect,useMemo,useRef,useState} from 'react';
import {Link,useNavigate} from 'react-router-dom';
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {Play,Square,Radio,Search,ScanSearch,RotateCcw,CircleCheck,CircleAlert,LoaderCircle,Eraser} from 'lucide-react';
import {api,num,human} from '../api';
import {Badge} from '../components/common';
import {Button} from '../components/ui/button';
import type {StreamEvent,StreamSession,ClaimCoverage} from '../types';

export const ACTIVE=['STARTING','RUNNING','STOPPING','DRAINING'];
const FEED_LIMIT=300;
const TERMINAL=['COMPLETED','NOT_APPLICABLE','INSUFFICIENT_DATA'];
export function useStreamCurrent(){return useQuery<{session:StreamSession|null;runner_alive:boolean;defaults:any}>({queryKey:['/stream'],queryFn:()=>api('/stream'),refetchInterval:q=>ACTIVE.includes(q.state.data?.session?.status||'')?1500:8000,retry:1});}

/** Where an event leads: claim detail, SupplyTrace, case workspace, or the provider's radar / network / forecast view. */
export function eventTarget(e:StreamEvent):string|null{
 const t=e.event_type;
 if(['case_created','case_updated','priority_recalculated'].includes(t)&&e.case_id)return '/cases/'+e.case_id;
 if(t==='finding_created'&&e.payload?.finding_type==='stolen_id_batch')return '/radar/'+e.payload.entity_id;
 if(t.startsWith('supplytrace')&&e.claim_id)return '/supplytrace/'+e.claim_id;
 if(t==='finding_created'&&e.payload?.engine==='supplytrace'&&e.claim_id)return '/supplytrace/'+e.claim_id;
 if(t==='member_radar_completed'&&e.provider_id)return e.payload?.radar_candidate?'/radar/'+e.provider_id:'/network?entity='+e.provider_id;
 if(['phoenix_completed','nexus_graph_completed','cohort_provider'].includes(t)&&e.provider_id)return '/network?entity='+e.provider_id;
 if(['isolation_forest_completed','forecast_completed'].includes(t)&&e.provider_id)return '/forecast?provider='+e.provider_id;
 if(e.claim_id)return '/claims/'+e.claim_id;
 if(e.case_id)return '/cases/'+e.case_id;
 if(e.provider_id)return '/network?entity='+e.provider_id;
 return null;
}
const tone=(t:string)=>t==='processing_failed'||t==='claim_rejected'?'bad':['finding_created','case_created','case_updated','priority_recalculated'].includes(t)?'signal':t==='claim_fully_analyzed'||t==='session_completed'?'good':t.startsWith('enrichment')||t.endsWith('_completed')&&!t.startsWith('claim')?'batch':'plain';

/** Cursor polling of committed events: one request at a time, IDs strictly increasing, duplicates dropped, bounded list. */
export function useEventFeed(sessionId:string|undefined,active:boolean){
 const [events,setEvents]=useState<StreamEvent[]>([]);const [error,setError]=useState('');const cursor=useRef(0);const live=useRef(active);live.current=active;
 useEffect(()=>{setEvents([]);cursor.current=0;if(!sessionId)return;let alive=true;let timer:ReturnType<typeof setTimeout>;
  const tick=async()=>{try{let more=true;while(more&&alive){const r=await api<{items:StreamEvent[];cursor:number;more:boolean}>(`/stream/events?session_id=${encodeURIComponent(sessionId)}&after=${cursor.current}&limit=200`);if(!alive)return;if(r.items.length){cursor.current=Math.max(cursor.current,r.cursor);setEvents(prev=>{const seen=new Set(prev.map(e=>e.event_id));const fresh=r.items.filter(e=>!seen.has(e.event_id)).reverse();return [...fresh,...prev].slice(0,FEED_LIMIT);});}more=r.more;}setError('');}catch(e){setError(e instanceof Error?e.message:String(e));}if(alive)timer=setTimeout(tick,live.current?1500:6000);};
  tick();return()=>{alive=false;clearTimeout(timer);};},[sessionId]);
 return {events,error};
}

function Metric({label,value,note,tone}:{label:string;value:React.ReactNode;note?:string;tone?:string}){return <div className={'live-metric '+(tone||'')}><span>{label}</span><strong>{value}</strong>{note&&<small>{note}</small>}</div>;}
const time=(v?:string|null)=>v?new Date(v).toLocaleTimeString():'—';

export function LiveMonitor(){
 const client=useQueryClient();const navigate=useNavigate();
 const current=useStreamCurrent();const sessions=useQuery<any>({queryKey:['/stream/sessions?limit=10'],queryFn:()=>api('/stream/sessions?limit=10'),refetchInterval:10000});
 const [viewId,setViewId]=useState('');const [rate,setRate]=useState('1');const [count,setCount]=useState('60');const [mode,setMode]=useState('mixed_demo');
 const [query,setQuery]=useState('');const [kind,setKind]=useState('all');const [clearedAt,setClearedAt]=useState(0);const [claimId,setClaimId]=useState<string|null>(null);
 const latest=current.data?.session||null;const sid=viewId||latest?.session_id;
 const viewed=useQuery<StreamSession>({queryKey:['/stream/sessions/'+sid],queryFn:()=>api('/stream/sessions/'+sid),enabled:!!viewId,refetchInterval:q=>ACTIVE.includes(q.state.data?.status||'')?1500:false});
 const session:StreamSession|null|undefined=viewId?viewed.data:latest;const active=!!session&&ACTIVE.includes(session.status);
 const {events,error}=useEventFeed(sid,active);
 const claims=useQuery<any>({queryKey:['/stream/sessions/'+sid+'/claims?page_size=12'],queryFn:()=>api(`/stream/sessions/${sid}/claims?page_size=12`),enabled:!!sid,refetchInterval:active?2000:false});
 const start=useMutation({mutationFn:()=>api('/stream/sessions',{rate_per_second:Number(rate),max_claims:Number(count),scenario_mode:mode}),onSuccess:()=>{setViewId('');setClaimId(null);setClearedAt(0);client.invalidateQueries({queryKey:['/stream']});client.invalidateQueries({queryKey:['/stream/sessions?limit=10']});}});
 const stop=useMutation({mutationFn:(id:string)=>api(`/stream/sessions/${id}/stop`,{}),onSuccess:()=>client.invalidateQueries({queryKey:['/stream']})});
 const shown=useMemo(()=>events.filter(e=>e.event_id>clearedAt).filter(e=>kind==='all'||(kind==='signals'?tone(e.event_type)==='signal':kind==='failures'?tone(e.event_type)==='bad':kind==='claims'?e.event_type.startsWith('claim'):tone(e.event_type)==='batch')).filter(e=>!query||`${e.message} ${e.claim_id||''} ${e.case_id||''} ${e.provider_id||''}`.toLowerCase().includes(query.toLowerCase())),[events,kind,query,clearedAt]);
 const m=session?.metrics;const defaults=current.data?.defaults;const runnerUp=current.data?.runner_alive;
 return <section className="card live-monitor" aria-label="Live Claims Monitor">
  <div className="card-head"><div><h2><Radio size={16}/> Live Claims Monitor</h2><p>Watch synthetic healthcare claims move through ClaimShield Nexus in real time.</p></div>
   <div className="live-controls">
    <label>Rate<select value={rate} onChange={e=>setRate(e.target.value)} disabled={active} aria-label="Claims per second">{['0.5','1','2'].map(v=><option key={v} value={v}>{v}/s</option>)}</select></label>
    <label>Claims<select value={count} onChange={e=>setCount(e.target.value)} disabled={active} aria-label="Claims in session">{['30','60','90','120'].map(v=><option key={v} value={v}>{v}</option>)}</select></label>
    <label>Mode<select value={mode} onChange={e=>setMode(e.target.value)} disabled={active} aria-label="Scenario mode"><option value="mixed_demo">Mixed demo</option><option value="normal_only">Ordinary only</option></select></label>
    <Button size="sm" onClick={()=>start.mutate()} disabled={active||start.isPending||runnerUp===false}>{start.isPending?<LoaderCircle size={14} className="spin"/>:<Play size={14}/>}Start Live Simulation</Button>
    <Button size="sm" variant="outline" onClick={()=>latest&&stop.mutate(latest.session_id)} disabled={!latest||!ACTIVE.includes(latest.status)||latest.stop_requested||stop.isPending}><Square size={13}/>Stop Simulation</Button>
   </div></div>
  <div className="live-strip">
   <span className={'live-dot '+(active?'on':'')}/><strong>{session?session.session_id:'No session yet'}</strong>{session&&<Badge value={session.status}/>}
   <span className="live-pack">{(session?.config?.pack_version||defaults?.pack_version||'live-stream-pack-v1')} · {human(session?.config?.scenario_mode||mode)} · seed {session?.seed??defaults?.seed??'—'} · synthetic records only</span>
   <select className="live-session" value={viewId} onChange={e=>{setViewId(e.target.value);setClaimId(null);setClearedAt(0);}} aria-label="Select session"><option value="">Latest session</option>{(sessions.data?.items||[]).map((s:any)=><option key={s.session_id} value={s.session_id}>{s.session_id} · {human(s.status)} · {s.generated_count}/{s.intended_count}</option>)}</select>
   <Button size="sm" variant="ghost" onClick={()=>setClearedAt(events[0]?.event_id||0)} title="Hide the current feed entries (nothing is deleted)"><Eraser size={13}/>Clear display</Button>
  </div>
  {runnerUp===false&&<div className="live-alert">The stream runner service is not running, so a session cannot start. Run <code>docker compose up -d stream</code>.</div>}
  {(start.error||stop.error)&&<div className="live-alert" role="alert">{(start.error||stop.error)?.message}</div>}
  {m?<div className="live-metrics">
   <Metric label="Generated" value={num(m.generated)} note={`of ${num(session!.intended_count)}${m.rejected?` · ${m.rejected} rejected`:''}`}/>
   <Metric label="Accepted" value={num(m.accepted)} note="committed to PostgreSQL"/>
   <Metric label="Analyzed" value={num(m.analyzed)} note="immediate screening done"/>
   <Metric label="Fully analyzed" value={num(m.fully_analyzed)} note="all 12 stages terminal" tone="good"/>
   <Metric label="Pending" value={num(m.pending)} note={`${m.awaiting_immediate} screening · ${m.awaiting_enrichment} enrichment`} tone={m.pending?'warn':''}/>
   <Metric label="Failed" value={num(m.failed)} note={`${m.worker_errors} processing error event(s)`} tone={m.failed?'bad':''}/>
   <Metric label="New findings" value={num(m.new_findings)} note="from existing engines" tone={m.new_findings?'signal':''}/>
   <Metric label="Cases" value={`${m.cases_created} new · ${m.cases_updated} upd.`} note="persisted case changes"/>
   <Metric label="Rate" value={m.arrival_rate_per_second!=null?`${m.arrival_rate_per_second}/s`:'—'} note={m.throughput_per_minute!=null?`${m.throughput_per_minute} fully analyzed/min`:'throughput pending'}/>
   <Metric label="Backlog" value={`${m.pending} / max ${m.max_backlog}`} note={`oldest pending ${m.oldest_pending_age_seconds}s`}/>
   <Metric label="Micro-batch" value={m.micro_batch_seconds.last!=null?`${m.micro_batch_seconds.last.toFixed(1)}s`:'—'} note={`${m.enrichment_runs} run(s) · mean ${m.micro_batch_seconds.mean??'—'}s`}/>
   <Metric label="Latest analysis" value={time(m.latest_fully_analyzed_at)} note={m.end_to_end_latency_s.mean!=null?`end-to-end mean ${m.end_to_end_latency_s.mean}s · p95 ${m.end_to_end_latency_s.p95}s`:'no claim fully analyzed yet'}/>
  </div>:<div className="empty">No live session has run yet. Start a simulation to stream synthetic claims through the real pipeline.</div>}
  {session&&<div className="live-grid">
   <div className="live-feed">
    <div className="live-feed-head"><div className="search-input"><Search size={14}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search events, claims, cases…" aria-label="Search events"/></div>
     <div className="live-chips" role="tablist">{[['all','All'],['claims','Claims'],['signals','Findings & cases'],['batch','Enrichment'],['failures','Failures']].map(([k,l])=><button key={k} className={kind===k?'active':''} onClick={()=>setKind(k)}>{l}</button>)}</div></div>
    {error&&<div className="live-alert">Event feed: {error} (retrying)</div>}
    <ol className="live-events" aria-label="Live event feed">{shown.map(e=>{const target=eventTarget(e);return <li key={e.event_id} className={'ev '+tone(e.event_type)+(target?' clickable':'')} onClick={()=>target&&navigate(target)}>
     <time>{new Date(e.created_at).toLocaleTimeString()}</time><span className="ev-type">{human(e.event_type)}</span><span className="ev-msg">{e.message}</span>
     {e.claim_id&&<button className="ev-inspect" title="Inspect pipeline stages" aria-label={`Inspect ${e.claim_id}`} onClick={x=>{x.stopPropagation();setClaimId(e.claim_id!);}}><ScanSearch size={13}/></button>}</li>;})}
     {!shown.length&&<li className="ev plain"><span className="ev-msg">{events.length?'No events match this filter.':'Waiting for committed events…'}</span></li>}</ol>
    <small className="live-foot">Showing up to {FEED_LIMIT} most recent committed events · click an event to open its claim, case or provider view.</small>
   </div>
   <div className="live-side">
    <ClaimInspector claimId={claimId} onClose={()=>setClaimId(null)}/>
    <div className="live-claims"><h3>Recent claims</h3>{(claims.data?.items||[]).map((c:any)=><button key={c.claim_id} className={claimId===c.claim_id?'active':''} onClick={()=>setClaimId(c.claim_id)}><span className="mono">#{c.sequence}</span><strong>{c.claim_id}</strong><small>{c.scenario_label}</small><Badge value={c.analysis_status}/></button>)}{!claims.data?.items?.length&&<p className="muted">No accepted claims yet.</p>}</div>
   </div>
  </div>}
 </section>;
}

export function ClaimInspector({claimId,onClose}:{claimId:string|null;onClose:()=>void}){
 const client=useQueryClient();
 const q=useQuery<ClaimCoverage>({queryKey:['/stream/claims/'+claimId+'/coverage'],queryFn:()=>api(`/stream/claims/${claimId}/coverage`),enabled:!!claimId,refetchInterval:x=>x.state.data&&['FULLY_ANALYZED','FAILED'].includes(x.state.data.analysis_status)?false:1500});
 const retry=useMutation({mutationFn:()=>api(`/stream/claims/${claimId}/retry`,{}),onSuccess:()=>client.invalidateQueries({queryKey:['/stream/claims/'+claimId+'/coverage']})});
 if(!claimId)return <div className="inspector empty-inspector"><ScanSearch size={18}/><p>Select a claim (or the scan icon on an event) to inspect its pipeline stages.</p></div>;
 if(q.isPending)return <div className="inspector"><LoaderCircle className="spin" size={16}/> Loading stages…</div>;
 if(q.error)return <div className="inspector live-alert">{q.error.message}</div>;
 const c=q.data!;
 return <div className="inspector" aria-label="Pipeline inspector">
  <div className="inspector-head"><div><Link to={'/claims/'+c.claim_id} className="text-link"><strong>{c.claim_id}</strong></Link><small>#{c.sequence} · {c.scenario_label} · service {c.service_date}</small></div>
   {c.fully_analyzed?<span className="full-badge"><CircleCheck size={13}/>Fully analyzed</span>:c.analysis_status==='FAILED'?<span className="fail-badge"><CircleAlert size={13}/>Failed</span>:<span className="wait-badge"><LoaderCircle size={13} className="spin"/>{human(c.analysis_status)}</span>}
   <button className="ev-inspect" onClick={onClose} aria-label="Close inspector">×</button></div>
  <ol className="stages">{c.stages.map(s=><li key={s.stage} className={'stage '+s.status.toLowerCase()}>
   <div className="stage-top"><span className="stage-name">{s.label}</span><span className="stage-layer">{s.layer}</span><Badge value={s.status}/></div>
   {(s.reason||s.error)&&<p className={s.error?'stage-error':''}>{s.error||s.reason}</p>}
   <small>{s.completed_at?`done ${new Date(s.completed_at).toLocaleTimeString()}`:TERMINAL.includes(s.status)?'':'not finished'}{s.findings_count?` · ${s.findings_count} finding(s)`:''}{s.attempts>1?` · attempt ${s.attempts}`:''}</small>
  </li>)}</ol>
  {c.analysis_status==='FAILED'&&<Button size="sm" variant="outline" onClick={()=>retry.mutate()} disabled={retry.isPending}><RotateCcw size={13}/>Retry failed stages</Button>}
  <small className="live-foot">{c.scenario_note} {c.enrichment_run?`Enrichment run ${c.enrichment_run.run_id} (covers sequence ≤ ${c.enrichment_run.watermark_sequence}).`:''}</small>
 </div>;
}

/** App-level: while a session is active, targeted invalidation keeps every mounted page on committed data. */
export function StreamSync(){
 const client=useQueryClient();const q=useStreamCurrent();const prev=useRef<any>(null);
 useEffect(()=>{const s=q.data?.session;if(!s?.metrics)return;const m=s.metrics;const now={sid:s.session_id,accepted:m.accepted,signals:m.new_findings+m.cases_created+m.cases_updated,runs:m.enrichment_runs,full:m.fully_analyzed,status:s.status};const p=prev.current;prev.current=now;if(!p||p.sid!==now.sid)return;
  const touch=(prefixes:string[])=>client.invalidateQueries({predicate:x=>typeof x.queryKey[0]==='string'&&prefixes.some(pr=>(x.queryKey[0] as string).startsWith(pr))});
  if(now.accepted!==p.accepted)touch(['/dataset/summary','/claims','/batches','/stream/sessions/']);
  if(now.signals!==p.signals||now.full!==p.full)touch(['/siu/queue','/cases','/claims/','/supplytrace']);
  if(now.runs!==p.runs||now.status!==p.status)touch(['/radar','/network','/providers','/members','/evaluation','/models']);
 },[q.data,client]);
 return null;
}
