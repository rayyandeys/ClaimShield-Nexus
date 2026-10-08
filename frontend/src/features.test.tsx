import {describe,it,expect,vi,afterEach} from 'vitest';
import {render,screen,fireEvent,cleanup,within,act} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import App from './App';
vi.mock('echarts',()=>({init:()=>({setOption:vi.fn(),resize:vi.fn(),dispose:vi.fn()})}));
// Captures what the real component hands to Cytoscape, and its tap handler, so tests can select an edge.
const cy=vi.hoisted(()=>({elements:[] as any[],tap:null as null|((e:any)=>void)}));
vi.mock('cytoscape',()=>({default:(options:any)=>{cy.elements=options.elements;return {layout:()=>({run:vi.fn()}),on:(_event:string,_selector:string,handler:(e:any)=>void)=>{cy.tap=handler;},resize:vi.fn(),destroy:vi.fn(),fit:vi.fn(),zoom:vi.fn(()=>1)};}}));
globalThis.ResizeObserver=class{observe(){}disconnect(){}unobserve(){}};

const batchFinding={finding_id:'F-BATCH',finding_type:'stolen_id_batch',engine:'radar',entity_type:'provider',entity_id:'P0252',related_claim_ids:['C1','C2'],related_provider_ids:['P0252'],related_facility_ids:['F1'],severity:'MEDIUM',anomaly_score_or_rule_result:8.6,rule_or_model_version:'member-radar-1.0',evidence_ids:['E1'],explanation:'270 previously unrelated members.',data_completeness:.8,limitations:['Requires confirmation.'],detected_at:'2026-10-08',status:'ACTIVE'};
const caseData={case_id:'CASE-B',title:'P0252 · member batch',summary:'s',primary_entity:'P0252',case_status:'NEW',severity:'MEDIUM',priority_score:70,potential_financial_exposure:1000,payment_workflow:'POSTPAYMENT',assigned_investigator:null,claims:[{claim_id:'C1',member_id:'M1',service_date:'2026-09-01',primary_code:'SIM-DME-CATHETER',paid_amount_usd:300,billed_amount_usd:500},{claim_id:'C2',member_id:'M2',service_date:'2026-09-02',primary_code:'SIM-DME-CATHETER',paid_amount_usd:300,billed_amount_usd:500}],findings:[batchFinding],evidence:[],ranking:{factors:{severity:18}},allowed_transitions:['UNDER_REVIEW'],timeline:[]};
const outcome=(priority:number,queue_position:number|null,in_capacity:boolean)=>({priority,queue_position,in_capacity});
const nextEvidence={case_id:'CASE-B',hypothetical:true,capacity:10,current:{...outcome(70,4,true),stored_priority:70,queue_size:900},recommendations:[{rank:1,finding_id:'F-BATCH',finding_type:'stolen_id_batch',finding_severity:'MEDIUM',evidence_type:'member_confirmation',label:'Member confirmation (simulated)',if_explains:'The member confirms receiving the item.',if_supports:'The member reports not receiving it.',subject_claim_id:'C1',estimated_days:7,p_benign:.2,probability_source:'synthetic_default_assumption',history_count:0,outcomes:{explains:outcome(55,14,false),supports:outcome(72,3,true)},expected_directed_delta:4.6,expected_absolute_shift:4.6,decision_change_probability:.2,crosses_capacity_boundary:true,legacy_voi:.66,decision_value:.29,open_requests:0}],considered:1,outstanding:[],evidence_version:1,scorer:'app.services.cases.rank_values (policy siu-1.0)',formula:'decision_value = …',catalog_version:'evidence-catalog-1.0',probability_note:'Synthetic assumptions.',notice:'Hypothetical estimates.'};
const simulation={hypothetical:true,label:'HYPOTHETICAL — nothing was saved',finding_id:'F-BATCH',outcome:'explains',capacity:10,original:{...outcome(70,4,true),factors:{severity:18}},simulated:{...outcome(55,14,false),factors:{severity:0},severity:'LOW',exposure:0},entering_top_k:[{case_id:'CASE-X',title:'P0099 · next case'}],leaving_top_k:[{case_id:'CASE-B',title:'P0252 · member batch'}]};
const link={link_id:'PS-1',predecessor_id:'P0251',successor_id:'P0252',predecessor_name:'Synthetic Provider 251',successor_name:'Synthetic Provider 252',score:.82,flagged:true,components:{patient_overlap:.71,shared_identifiers:.9,billing_similarity:1,referral_overlap:.67,timing:.87},details:{predecessor_status:'revoked',predecessor_status_date:'2026-03-01',days_between:23,matched_identifiers:{phone:{}},compared_identifiers:['phone'],shared_member_count:30,predecessor_members:40,successor_members:32,shared_procedure_codes:['SIM-DME-SUPPLY'],shared_referrers:['P0261'],unavailable_components:[],documented_acquisition:null,predecessor_investigation_history:['INV00151']},finding_id:'F-PH',case_id:'CASE-B',detector_version:'phoenix-1.0'};
const confirmation={confirmation_id:'MC-1',case_id:'CASE-B',finding_id:'F-BATCH',claim_id:'C1',member_id:'M1',request_id:'ER-1',request_status:'REQUESTED',notice_text:'SIMULATED NOTICE — ClaimShield prototype. No message was sent to any person.',created_by:'Demo',simulated:true,response:'PENDING',response_at:null,notes:null,created_at:'2026-10-08'};

type Override=(url:string,method:string,body:any)=>{status?:number;data?:any;pending?:boolean}|undefined;
function setup(path:string,override?:Override){const calls:{url:string;method:string;body:any}[]=[];vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{const method=init?.method||'GET';const body=init?.body?JSON.parse(String(init.body)):undefined;calls.push({url,method,body});
  const custom=override?.(url,method,body);if(custom?.pending)return new Promise(()=>{});if(custom)return {ok:(custom.status||200)<400,status:custom.status||200,json:async()=>custom.data};
  const data=url.includes('/simulate')?simulation:url.includes('/next-evidence')?(url.includes('/siu/')?{items:[]}:nextEvidence):url.includes('/predecessors')?{items:[link]}:url.includes('/successors')?{items:[]}:url.includes('/confirmations/MC-1/response')?{...confirmation,response:'NO'}:url.includes('/confirmations')?{items:[confirmation],simulated:true}:url.includes('/evidence-requests')?{items:[]}:url.includes('/cases/CASE-B')?caseData
  :url.includes('/radar/batches?')?{items:[{provider_id:'P0252',provider_name:'Synthetic Provider 252',specialty:'DME Supplier',qualified:true,metrics:{new_members:270,growth_ratio:8.6,share_without_prior_relationship:.97,share_with_prior_care_context:0,geographic_dispersion_percentile:.43}}],total:1,page:1,page_size:10}:url.includes('/radar/members')?{items:[],total:0,page:1,page_size:10}
  :url.includes('/radar/batches/P0252/members')?{items:[],total:0}:url.includes('/radar/batches/P0252/graph')?{nodes:[],edges:[],truncated:false}:url.includes('/radar/batches/P0252')?{provider_id:'P0252',qualified:true,provider:{provider_name:'Synthetic Provider 252'},case_id:'CASE-B',detector_version:'member-radar-1.0',metrics:{new_members:270,recent_claims:270,historical_rate_per_window:31,baseline_new_members:32,baseline_active_days:92,recent_window:['2026-07-03','2026-09-30'],baseline_window:['2025-07-03','2026-07-02']},checks:[{name:'new_members',value:270,op:'>=',threshold:25,required:true,evaluable:true,passed:true},{name:'geographic_dispersion_percentile',value:.43,op:'>',threshold:.75,required:false,evaluable:true,passed:false}],config:{notes:['Prototype heuristics.']},acquisition:[]}:{items:[],total:0};
  return {ok:true,json:async()=>data};}));const client=new QueryClient({defaultOptions:{queries:{retry:false}}});render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App/></MemoryRouter></QueryClientProvider>);return calls;}
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

describe('Investigation extensions',()=>{
 it('lists Member Radar batches from the API and opens a batch',async()=>{setup('/radar');fireEvent.click(await screen.findByText('P0252'));expect(await screen.findByText('Threshold checks')).toBeInTheDocument();expect(screen.getByText('Corroborating')).toBeInTheDocument();expect(screen.getByRole('link',{name:/Open consolidated case/})).toHaveAttribute('href','/cases/CASE-B');});
 it('simulates an outcome without any write request',async()=>{const calls=setup('/cases/CASE-B');fireEvent.click(await screen.findByRole('tab',{name:/next evidence/i}));fireEvent.click(await screen.findByRole('button',{name:/Simulate explains finding/}));const preview=await screen.findByRole('region',{name:'Hypothetical simulation'});expect(within(preview).getByText('HYPOTHETICAL — nothing was saved')).toBeInTheDocument();expect(within(preview).getByText('P0099 · next case')).toBeInTheDocument();const posts=calls.filter(c=>c.method==='POST');expect(posts.map(c=>c.url)).toEqual(['/api/v1/cases/CASE-B/simulate']);expect(posts[0].body).toMatchObject({finding_id:'F-BATCH',outcome:'explains',capacity:10});});
 it('shows probability source and capacity crossing',async()=>{setup('/cases/CASE-B');fireEvent.click(await screen.findByRole('tab',{name:/next evidence/i}));expect(await screen.findByText(/Synthetic default assumption/)).toBeInTheDocument();expect(screen.getAllByText(/crosses boundary/).length).toBeGreaterThan(0);});
 it('records a simulated member response through the backend',async()=>{const calls=setup('/cases/CASE-B');fireEvent.click(await screen.findByRole('tab',{name:/confirmations/i}));expect(await screen.findByText(/No message was sent to any person/)).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'No'}));await vi.waitFor(()=>expect(calls.some(c=>c.method==='POST'&&c.url.endsWith('/confirmations/MC-1/response')&&c.body.response==='NO')).toBe(true));});
 it('sends typed edges to the graph and shows actual Phoenix components when the edge is selected',async()=>{
  const edge={id:'PS-1',source:'P0251',target:'P0252',type:'possible_successor',score:.82,verification_status:'algorithmic_similarity'};
  const network={nodes:[{data:{id:'P0251',label:'Provider 251',type:'provider'}},{data:{id:'P0252',label:'Provider 252',type:'provider'}}],edges:[{data:edge},{data:{id:'REL1',source:'P0252',target:'F1',type:'affiliated_with'}}],cases:[],truncated:false};
  setup('/network?entity=P0252',url=>url.includes('/network/P0252')?{data:network}:url.includes('/phoenix/links/PS-1')?{data:link}:undefined);
  expect(await screen.findByText(/2 nodes · 2 edges/)).toBeInTheDocument();
  expect(cy.elements.filter((e:any)=>e.data.source).map((e:any)=>e.data.type)).toEqual(['possible_successor','affiliated_with']);
  await act(async()=>cy.tap!({target:{data:()=>edge}}));
  expect(await screen.findByText('0.82')).toBeInTheDocument();
  for(const v of ['0.71','0.90','1.00','0.67','0.87'])expect(screen.getByText(v)).toBeInTheDocument();
  expect(screen.getByText(/INV00151 · not transferred to the successor/)).toBeInTheDocument();
 });
 it('shows pending and error states for Next-Best-Evidence',async()=>{
  setup('/cases/CASE-B',url=>url.includes('/cases/CASE-B/next-evidence')?{pending:true}:undefined);
  fireEvent.click(await screen.findByRole('tab',{name:/next evidence/i}));
  expect(await screen.findByText('Loading investigation data…')).toBeInTheDocument();
  cleanup();vi.unstubAllGlobals();
  setup('/cases/CASE-B',url=>url.includes('/cases/CASE-B/next-evidence')?{status:404,data:{detail:'Case not found'}}:undefined);
  fireEvent.click(await screen.findByRole('tab',{name:/next evidence/i}));
  expect(await screen.findByText('Could not load data',{},{timeout:3000})).toBeInTheDocument();expect(screen.getByText('Case not found')).toBeInTheDocument();
 });
 it('refreshes the case and shows the new audit action after a reviewed outcome',async()=>{
  const state={timeline:[] as any[],status:'RECEIVED'};
  const request={request_id:'ER-9',case_id:'CASE-B',finding_id:'F-BATCH',evidence_type:'member_confirmation',subject_id:'C1',status:'RECEIVED',requested_by:'Demo',estimated_days:7,estimated_p_benign:.2,probability_source:'synthetic_default_assumption',reviewer:null,review_notes:null,evidence_id:null,created_at:'2026-10-08T10:00:00Z',outcome_at:null};
  const calls=setup('/cases/CASE-B',(url,method,body)=>{
   if(method==='POST'&&url.endsWith('/evidence-requests/ER-9/outcome')){state.status=body.status;state.timeline=[{event_id:'EV-1',action_type:'evidence_outcome_reviewed',actor:'Demo Analyst · Team CIPHER',explanation:body.notes,created_at:'2026-10-08T10:05:00Z',previous_state:{status:'RECEIVED'},new_state:{status:body.status}}];return {data:{...request,status:body.status}};}
   if(url.includes('/cases/CASE-B/evidence-requests'))return {data:{items:[{...request,status:state.status}]}};
   if(url.endsWith('/cases/CASE-B'))return {data:{...caseData,timeline:state.timeline}};
   return undefined;});
  fireEvent.click(await screen.findByRole('tab',{name:/requests/i}));
  fireEvent.change(await screen.findByLabelText('Review notes'),{target:{value:'Member reported not receiving the catheter supplies.'}});
  const caseFetches=()=>calls.filter(c=>c.method==='GET'&&c.url.endsWith('/cases/CASE-B')).length;const before=caseFetches();
  fireEvent.click(screen.getByRole('button',{name:'Record reviewed outcome'}));
  await vi.waitFor(()=>expect(caseFetches()).toBeGreaterThan(before));
  expect(calls.find(c=>c.method==='POST')?.body).toEqual({status:'VERIFIED_SUPPORTS',notes:'Member reported not receiving the catheter supplies.'});
  fireEvent.click(screen.getByRole('tab',{name:/timeline/i}));
  expect(await screen.findByText('evidence outcome reviewed')).toBeInTheDocument();expect(screen.getByText('Member reported not receiving the catheter supplies.')).toBeInTheDocument();
 });
 it('shows the possible-successor banner and breakdown in the case',async()=>{setup('/cases/CASE-B');expect(await screen.findByText(/Possible successor relationship:/)).toBeInTheDocument();expect(screen.getByRole('link',{name:/View graph edge/})).toHaveAttribute('href','/network?entity=P0252');});
});
