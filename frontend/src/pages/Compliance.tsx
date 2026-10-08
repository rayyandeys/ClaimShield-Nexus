import {useState} from 'react';
import {useMutation,useQueryClient} from '@tanstack/react-query';
import {ShieldCheck,FileQuestion,Eye,Search,Send,Lock} from 'lucide-react';
import {api,useApi,money,human} from '../api';
import {State,Card,Badge,Notice} from '../components/common';
import {Button} from '../components/ui/button';

const ICONS:Record<string,any>={request_more_evidence:FileQuestion,monitor_provider:Eye,full_investigation:Search,external_referral:Send};
export const READINESS_LABEL:Record<string,string>={MORE_EVIDENCE_NEEDED:'More evidence needed',INVESTIGATION_REVIEW_ELIGIBLE:'Investigation review eligible',APPROVAL_REQUIRED:'Approval required',MONITORING:'Monitoring',NO_ACTION_ELIGIBLE:'No action eligible'};
export function Readiness({value}:{value?:string|null}){return value?<span className={'badge readiness-badge '+value.toLowerCase()}>{READINESS_LABEL[value]||human(value)}</span>:<span className="muted">—</span>;}

export function CompliancePanel({caseId}:{caseId:string}){
 const client=useQueryClient();const q=useApi(`/cases/${caseId}/policy`);const [action,setAction]=useState('');const [text,setText]=useState('');
 const submit=useMutation({mutationFn:()=>api(`/cases/${caseId}/decisions`,{action,justification:text}),onSuccess:()=>{setText('');setAction('');client.invalidateQueries();}});
 return <State query={q}>{d=>{const selectable=d.actions.filter((a:any)=>['ALLOWED','REQUIRES_APPROVAL'].includes(a.status));return <>
  <Card title="Compliance & decisions" subtitle={`Policy ${d.policy_id} · internal demo policy`} action={<Readiness value={d.readiness}/>}>
   <div className="policy-summary">{[['Case',d.case.case_id],['Provider',d.case.provider_id],['SIU priority',Number(d.case.priority_score).toFixed(1)],['Active findings',d.case.active_findings+(d.case.explained_findings?` · ${d.case.explained_findings} explained`:'')],['Evidence review',`${d.case.evidence_review.reviewed} reviewed · ${d.case.evidence_review.open_requests} open`],['Review exposure',money(d.case.potential_financial_exposure)]].map(([k,v])=><div key={k}><span>{k}</span><strong>{v}</strong></div>)}</div>
  </Card>
  <div className="policy-grid">{d.actions.map((a:any)=>{const Icon=ICONS[a.action];return <article key={a.action} className={'policy-card '+a.status.toLowerCase()} aria-label={a.label}>
   <div className="policy-top"><Icon size={16}/><strong>{a.label}</strong><Badge value={a.status}/></div>
   <p>{a.reason}</p>{a.missing.length>0&&<ul>{a.missing.map((m:string)=><li key={m}>{m}</li>)}</ul>}
  </article>;})}</div>
  <div className="grid-main">
   <Card title="Record a recommendation">
    <div className="action-form">
     <label>Action<select aria-label="Decision action" value={action} onChange={e=>setAction(e.target.value)}><option value="">Choose an eligible action</option>{selectable.map((a:any)=><option key={a.action} value={a.action}>{a.label}{a.status==='REQUIRES_APPROVAL'?' (needs approval)':''}</option>)}</select></label>
     <label>Justification<textarea aria-label="Decision justification" rows={3} value={text} onChange={e=>setText(e.target.value)} placeholder="Evidence reviewed and reason (min. 10 characters)…"/></label>
     {submit.error&&<p className="error">{submit.error.message}</p>}
     {submit.data&&<p className="muted">Recorded as <Badge value={(submit.data as any).status}/></p>}
     <Button onClick={()=>submit.mutate()} disabled={!action||text.trim().length<10||submit.isPending}><ShieldCheck size={15}/>Submit recommendation</Button>
     <Notice><Lock size={13}/> {d.approval_note}</Notice>
    </div>
   </Card>
   <Card title="Decision history">{d.decisions.length?<div className="p-5">{d.decisions.map((x:any)=><div className="decision-row" key={x.decision_id}><div><strong>{human(x.action)}</strong><small>{x.requested_by} · {new Date(x.created_at).toLocaleString()} · {x.policy_version}</small><p className="muted">{x.justification}</p></div><Badge value={x.status}/></div>)}</div>:<div className="empty">No decisions yet.</div>}</Card>
  </div>
 </>;}}</State>;
}
